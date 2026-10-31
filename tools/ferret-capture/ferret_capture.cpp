// Captures raw frames from the Ferret's UVC streams over libusb.
//
// V4L2 cannot reach every format: the kernel does not know Y14. This tool talks
// standard UVC directly and writes unmodified frames plus a CSV index. It sends
// only UVC probe, commit and endpoint-reset requests, never vendor commands.
#include <libusb.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "uvc_payload.h"

namespace {

constexpr uint16_t kVendorId = 0x2bc5;
constexpr uint16_t kProductId = 0x069c;
constexpr int kTransferCount = 8;
constexpr unsigned kTransferTimeoutMs = 1000;
constexpr int kNoDataAbortSeconds = 5;
constexpr size_t kProbeSize = 34;

constexpr uint8_t kSetCur = 0x01;
constexpr uint8_t kGetCur = 0x81;
constexpr uint8_t kVsProbeControl = 0x01;
constexpr uint8_t kVsCommitControl = 0x02;
constexpr uint8_t kClassInterfaceOut = 0x21;
constexpr uint8_t kClassInterfaceIn = 0xA1;

constexpr uint8_t kCsInterface = 0x24;
constexpr uint8_t kVsFormatUncompressed = 0x04;
constexpr uint8_t kVsFrameUncompressed = 0x05;
constexpr uint8_t kVsFormatMjpeg = 0x06;
constexpr uint8_t kVsFrameMjpeg = 0x07;

std::atomic<bool> g_stop{false};

void on_signal(int) { g_stop = true; }

struct Frame {
    uint8_t index = 0;
    int width = 0;
    int height = 0;
    uint32_t max_buffer = 0;
    std::vector<uint32_t> intervals;
};

struct Format {
    uint8_t index = 0;
    std::string name;
    int bits_per_pixel = 0;
    std::vector<Frame> frames;
};

struct Stream {
    std::string camera;
    uint8_t control_interface = 0;
    uint8_t streaming_interface = 0;
    uint8_t endpoint = 0;
    std::vector<Format> formats;
};

uint16_t read16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }

uint32_t read32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

void write32(uint8_t* p, uint32_t value) {
    for (int i = 0; i < 4; i++) p[i] = uint8_t(value >> (8 * i));
}

std::string fourcc_name(const uint8_t* guid) {
    std::string name(reinterpret_cast<const char*>(guid), 4);
    while (!name.empty() && (name.back() == ' ' || name.back() == '\0')) name.pop_back();
    return name;
}

// Frame descriptors keep the same layout for uncompressed and MJPEG formats.
Frame parse_frame(const uint8_t* d, size_t length) {
    Frame frame;
    if (length < 26) return frame;
    frame.index = d[3];
    frame.width = read16(d + 5);
    frame.height = read16(d + 7);
    frame.max_buffer = read32(d + 17);
    const uint8_t interval_type = d[25];
    for (size_t i = 0; i < interval_type && 26 + 4 * i + 4 <= length; i++)
        frame.intervals.push_back(read32(d + 26 + 4 * i));
    return frame;
}

std::vector<Format> parse_formats(const uint8_t* extra, int extra_length) {
    std::vector<Format> formats;
    for (int offset = 0; offset + 3 <= extra_length;) {
        const uint8_t* d = extra + offset;
        const uint8_t length = d[0];
        if (length < 3 || offset + length > extra_length) break;
        offset += length;
        if (d[1] != kCsInterface) continue;

        const uint8_t subtype = d[2];
        if (subtype == kVsFormatUncompressed && length >= 27) {
            formats.push_back({d[3], fourcc_name(d + 5), d[21], {}});
        } else if (subtype == kVsFormatMjpeg && length >= 11) {
            formats.push_back({d[3], "MJPEG", 0, {}});
        } else if ((subtype == kVsFrameUncompressed || subtype == kVsFrameMjpeg) &&
                   !formats.empty()) {
            formats.back().frames.push_back(parse_frame(d, length));
        }
    }
    return formats;
}

std::vector<Stream> read_streams(libusb_device* device) {
    libusb_config_descriptor* config = nullptr;
    std::vector<Stream> streams;
    if (libusb_get_active_config_descriptor(device, &config) != 0) return streams;

    const char* cameras[] = {"depth", "ir", "rgb"};
    for (int i = 0; i < config->bNumInterfaces; i++) {
        const libusb_interface_descriptor& alt = config->interface[i].altsetting[0];
        if (alt.bInterfaceClass != LIBUSB_CLASS_VIDEO || alt.bInterfaceSubClass != 2) continue;
        if (alt.bNumEndpoints < 1 || streams.size() >= 3) continue;

        Stream stream;
        stream.camera = cameras[streams.size()];
        stream.streaming_interface = alt.bInterfaceNumber;
        stream.control_interface = alt.bInterfaceNumber - 1;
        stream.endpoint = alt.endpoint[0].bEndpointAddress;
        stream.formats = parse_formats(alt.extra, alt.extra_length);
        streams.push_back(std::move(stream));
    }
    libusb_free_config_descriptor(config);
    return streams;
}

const char* speed_name(int speed) {
    switch (speed) {
        case LIBUSB_SPEED_SUPER_PLUS: return "10 Gbit/s";
        case LIBUSB_SPEED_SUPER: return "5 Gbit/s";
        case LIBUSB_SPEED_HIGH: return "480 Mbit/s";
        default: return "slower than 480 Mbit/s";
    }
}

void print_streams(const std::vector<Stream>& streams) {
    for (const Stream& stream : streams) {
        printf("%s: interface %d, endpoint 0x%02x\n", stream.camera.c_str(),
               stream.streaming_interface, stream.endpoint);
        for (const Format& format : stream.formats) {
            printf("  %s (format %d, %d bits)\n", format.name.c_str(), format.index,
                   format.bits_per_pixel);
            for (const Frame& frame : format.frames) {
                printf("    %dx%d (frame %d, %u bytes):", frame.width, frame.height, frame.index,
                       frame.max_buffer);
                for (uint32_t interval : frame.intervals)
                    printf(" %.0f", 10000000.0 / interval);
                printf(" fps\n");
            }
        }
    }
}

struct Capture {
    FILE* data = nullptr;
    FILE* index = nullptr;
    std::vector<uint8_t> frame;
    size_t expected_size = 0;
    size_t max_payload = 0;
    bool have_frame = false;
    bool last_frame_id = false;
    bool frame_error = false;
    bool resyncing = false;
    uint32_t frame_pts = 0;
    uint64_t frames = 0;
    uint64_t dropped_frames = 0;
    uint64_t bad_headers = 0;
    uint64_t failed_transfers = 0;
    int active_transfers = 0;
    bool resubmit = true;
    std::chrono::steady_clock::time_point start;
    std::chrono::steady_clock::time_point first_frame;
    std::chrono::steady_clock::time_point last_frame;
};

void clear_frame(Capture& capture) {
    capture.frame.clear();
    capture.have_frame = false;
    capture.frame_error = false;
}

void finish_frame(Capture& capture) {
    if (!capture.have_frame) return;
    if (capture.frame_error || capture.frame.size() != capture.expected_size) {
        capture.dropped_frames++;
        clear_frame(capture);
        return;
    }

    const auto now = std::chrono::steady_clock::now();
    if (capture.frames == 0) capture.first_frame = now;
    capture.last_frame = now;

    const double ms = std::chrono::duration<double, std::milli>(now - capture.start).count();
    fwrite(capture.frame.data(), 1, capture.frame.size(), capture.data);
    fprintf(capture.index, "%llu,%.3f,%zu,%u,0\n", (unsigned long long)capture.frames, ms,
            capture.frame.size(), capture.frame_pts);

    capture.frames++;
    clear_frame(capture);
}

// Leftover data from an earlier stream can shift reads off the payload boundaries.
// A short transfer ends a payload, so the next one starts on a boundary again.
void handle_payload(Capture& capture, const uint8_t* data, size_t size) {
    if (size == 0) return;
    if (capture.resyncing) {
        if (size < capture.max_payload) capture.resyncing = false;
        return;
    }

    const PayloadHeader header = parse_payload_header(data, size);
    if (!header.valid) {
        capture.bad_headers++;
        if (capture.have_frame) capture.dropped_frames++;
        clear_frame(capture);
        capture.resyncing = size == capture.max_payload;
        return;
    }

    if (capture.have_frame && header.frame_id != capture.last_frame_id) finish_frame(capture);
    capture.last_frame_id = header.frame_id;
    capture.have_frame = true;
    if (header.has_pts) capture.frame_pts = header.pts;
    if (header.error) capture.frame_error = true;
    capture.frame.insert(capture.frame.end(), data + header.length, data + size);
    if (header.end_of_frame) finish_frame(capture);
}

void LIBUSB_CALL on_transfer(libusb_transfer* transfer) {
    Capture& capture = *static_cast<Capture*>(transfer->user_data);
    if (transfer->status == LIBUSB_TRANSFER_COMPLETED) {
        handle_payload(capture, transfer->buffer, transfer->actual_length);
    } else if (transfer->status != LIBUSB_TRANSFER_CANCELLED) {
        capture.failed_transfers++;
    }

    if (capture.resubmit && libusb_submit_transfer(transfer) == 0) return;
    capture.active_transfers--;
}

bool probe_and_commit(libusb_device_handle* handle, const Stream& stream, const Format& format,
                      const Frame& frame, uint32_t interval, uint32_t* max_payload) {
    uint8_t probe[kProbeSize] = {};
    probe[0] = 0x01;  // bmHint: keep dwFrameInterval fixed
    probe[2] = format.index;
    probe[3] = frame.index;
    write32(probe + 4, interval);

    const uint16_t interface = stream.streaming_interface;
    int result = libusb_control_transfer(handle, kClassInterfaceOut, kSetCur,
                                         kVsProbeControl << 8, interface, probe, kProbeSize, 1000);
    if (result != int(kProbeSize)) {
        fprintf(stderr, "probe SET_CUR failed: %s\n", libusb_error_name(result));
        return false;
    }

    result = libusb_control_transfer(handle, kClassInterfaceIn, kGetCur, kVsProbeControl << 8,
                                     interface, probe, kProbeSize, 1000);
    if (result != int(kProbeSize)) {
        fprintf(stderr, "probe GET_CUR failed: %s\n", libusb_error_name(result));
        return false;
    }

    printf("negotiated format %d, frame %d, interval %u, frame size %u, payload %u\n", probe[2],
           probe[3], read32(probe + 4), read32(probe + 18), read32(probe + 22));
    if (probe[2] != format.index || probe[3] != frame.index) {
        fprintf(stderr, "scanner did not accept the requested mode\n");
        return false;
    }

    result = libusb_control_transfer(handle, kClassInterfaceOut, kSetCur, kVsCommitControl << 8,
                                     interface, probe, kProbeSize, 1000);
    if (result != int(kProbeSize)) {
        fprintf(stderr, "commit SET_CUR failed: %s\n", libusb_error_name(result));
        return false;
    }
    *max_payload = read32(probe + 22);
    return true;
}

int run_capture(libusb_device_handle* handle, const Stream& stream, const Format& format,
                const Frame& frame, uint32_t interval, double seconds, const std::string& prefix) {
    const size_t expected_size = size_t(frame.width) * frame.height * format.bits_per_pixel / 8;

    uint32_t max_payload = 0;
    if (!probe_and_commit(handle, stream, format, frame, interval, &max_payload)) return 1;
    if (max_payload == 0) {
        fprintf(stderr, "scanner reported a zero payload size\n");
        return 1;
    }

    Capture capture;
    capture.expected_size = expected_size;
    capture.max_payload = max_payload;
    capture.frame.reserve(expected_size);
    capture.data = fopen((prefix + ".raw").c_str(), "wb");
    capture.index = fopen((prefix + ".csv").c_str(), "w");
    if (!capture.data || !capture.index) {
        fprintf(stderr, "cannot create output files for %s\n", prefix.c_str());
        return 1;
    }
    fprintf(capture.index, "frame,host_ms,bytes,pts,error\n");

    std::vector<std::vector<uint8_t>> buffers(kTransferCount, std::vector<uint8_t>(max_payload));
    std::vector<libusb_transfer*> transfers;
    capture.start = std::chrono::steady_clock::now();
    for (int i = 0; i < kTransferCount; i++) {
        libusb_transfer* transfer = libusb_alloc_transfer(0);
        libusb_fill_bulk_transfer(transfer, handle, stream.endpoint, buffers[i].data(),
                                  int(max_payload), on_transfer, &capture, kTransferTimeoutMs);
        transfers.push_back(transfer);
        const int result = libusb_submit_transfer(transfer);
        if (result != 0) {
            fprintf(stderr, "submit failed: %s\n", libusb_error_name(result));
            break;
        }
        capture.active_transfers++;
    }

    bool no_data = false;
    while (!g_stop && capture.active_transfers > 0) {
        timeval tv{0, 100000};
        libusb_handle_events_timeout_completed(nullptr, &tv, nullptr);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() -
                                                             capture.start).count();
        if (elapsed >= seconds) break;
        if (capture.frames == 0 && elapsed >= kNoDataAbortSeconds) {
            no_data = true;
            break;
        }
    }

    capture.resubmit = false;
    for (libusb_transfer* transfer : transfers) libusb_cancel_transfer(transfer);
    while (capture.active_transfers > 0) {
        timeval tv{0, 100000};
        libusb_handle_events_timeout_completed(nullptr, &tv, nullptr);
    }
    for (libusb_transfer* transfer : transfers) libusb_free_transfer(transfer);
    libusb_clear_halt(handle, stream.endpoint);

    fclose(capture.data);
    fclose(capture.index);

    const double span = std::chrono::duration<double>(capture.last_frame -
                                                      capture.first_frame).count();
    printf("frames %llu, %.2f fps, expected %zu bytes each\n", (unsigned long long)capture.frames,
           capture.frames > 1 && span > 0 ? (capture.frames - 1) / span : 0.0, expected_size);
    printf("dropped frames %llu, bad headers %llu, failed transfers %llu\n",
           (unsigned long long)capture.dropped_frames, (unsigned long long)capture.bad_headers,
           (unsigned long long)capture.failed_transfers);
    if (no_data) {
        fprintf(stderr, "no frames arrived within %d seconds\n", kNoDataAbortSeconds);
        return 1;
    }
    return capture.frames > 0 ? 0 : 1;
}

void usage() {
    fprintf(stderr,
            "usage: ferret-capture --list\n"
            "       ferret-capture --camera depth|ir --format Y14|Y10|Y8 --size WxH\n"
            "                      [--fps 30] [--seconds 3] --out PREFIX\n");
}

}  // namespace

int main(int argc, char** argv) {
    bool list = false;
    std::string camera = "depth", format_name, size, prefix;
    int fps = 30;
    double seconds = 3;

    for (int i = 1; i < argc; i++) {
        const std::string arg = argv[i];
        if (arg == "--list") {
            list = true;
        } else if (i + 1 >= argc) {
            usage();
            return 2;
        } else if (arg == "--camera") {
            camera = argv[++i];
        } else if (arg == "--format") {
            format_name = argv[++i];
        } else if (arg == "--size") {
            size = argv[++i];
        } else if (arg == "--fps") {
            fps = atoi(argv[++i]);
        } else if (arg == "--seconds") {
            seconds = atof(argv[++i]);
        } else if (arg == "--out") {
            prefix = argv[++i];
        } else {
            usage();
            return 2;
        }
    }
    if (!list && (format_name.empty() || size.empty() || prefix.empty())) {
        usage();
        return 2;
    }

    libusb_context* context = nullptr;
    int result = libusb_init(&context);
    if (result != 0) {
        fprintf(stderr, "libusb_init failed: %s\n", libusb_error_name(result));
        return 1;
    }
    std::unique_ptr<libusb_context, void (*)(libusb_context*)> context_guard(context, libusb_exit);

    libusb_device** devices = nullptr;
    const ssize_t count = libusb_get_device_list(nullptr, &devices);
    libusb_device* scanner = nullptr;
    for (ssize_t i = 0; i < count && !scanner; i++) {
        libusb_device_descriptor descriptor;
        if (libusb_get_device_descriptor(devices[i], &descriptor) == 0 &&
            descriptor.idVendor == kVendorId && descriptor.idProduct == kProductId)
            scanner = libusb_ref_device(devices[i]);
    }
    libusb_free_device_list(devices, 1);
    if (!scanner) {
        fprintf(stderr, "scanner not found\n");
        return 1;
    }

    const int speed = libusb_get_device_speed(scanner);
    printf("scanner on bus %d, device %d, link %s\n", libusb_get_bus_number(scanner),
           libusb_get_device_address(scanner), speed_name(speed));
    if (speed < LIBUSB_SPEED_SUPER)
        printf("warning: slow link, check the cable. Frame rates are limited to 15 fps.\n");

    const std::vector<Stream> streams = read_streams(scanner);
    if (list) {
        print_streams(streams);
        libusb_unref_device(scanner);
        return streams.empty() ? 1 : 0;
    }

    int width = 0, height = 0;
    if (sscanf(size.c_str(), "%dx%d", &width, &height) != 2) {
        usage();
        return 2;
    }

    const Stream* stream = nullptr;
    const Format* format = nullptr;
    const Frame* frame = nullptr;
    std::optional<uint32_t> interval;
    for (const Stream& s : streams)
        if (s.camera == camera) stream = &s;
    if (stream)
        for (const Format& f : stream->formats)
            if (f.name == format_name) format = &f;
    if (format)
        for (const Frame& f : format->frames)
            if (f.width == width && f.height == height) frame = &f;
    if (frame)
        for (uint32_t i : frame->intervals)
            if (uint32_t(10000000.0 / i + 0.5) == uint32_t(fps)) interval = i;
    if (!interval) {
        fprintf(stderr, "mode not offered: %s %s %s at %d fps\n", camera.c_str(),
                format_name.c_str(), size.c_str(), fps);
        return 1;
    }
    if (format->bits_per_pixel == 0) {
        fprintf(stderr, "only uncompressed formats are supported\n");
        return 1;
    }

    libusb_device_handle* handle = nullptr;
    result = libusb_open(scanner, &handle);
    libusb_unref_device(scanner);
    if (result != 0) {
        fprintf(stderr, "cannot open scanner: %s\n", libusb_error_name(result));
        return 1;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    // Auto-detach returns the interfaces to uvcvideo when they are released.
    libusb_set_auto_detach_kernel_driver(handle, 1);
    int status = 1;
    result = libusb_claim_interface(handle, stream->control_interface);
    if (result != 0) {
        fprintf(stderr, "cannot claim control interface: %s\n", libusb_error_name(result));
    } else {
        result = libusb_claim_interface(handle, stream->streaming_interface);
        if (result != 0) {
            fprintf(stderr, "cannot claim streaming interface: %s\n", libusb_error_name(result));
        } else {
            status = run_capture(handle, *stream, *format, *frame, *interval, seconds, prefix);
            libusb_release_interface(handle, stream->streaming_interface);
        }
        libusb_release_interface(handle, stream->control_interface);
    }
    libusb_close(handle);
    return status;
}
