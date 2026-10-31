// Shared library that streams the scanner's cameras over libusb.
//
// Frames go to a short queue for the app and, while recording, to a writer thread per camera
// that saves them untouched. A slow app can lose preview frames without losing recorded ones.
// Besides standard UVC requests, it only reads depth modes and writes a listed mode back.
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>

#include "depth_modes.h"
#include "scanner_link.h"
#include "uvc_descriptors.h"
#include "uvc_payload.h"

namespace {

constexpr int kTransfersPerStream = 8;
constexpr unsigned kTransferTimeoutMs = 1000;
constexpr size_t kProbeSize = 34;
constexpr size_t kAppQueueFrames = 6;
constexpr size_t kRecordQueueFrames = 600;
constexpr uint8_t kVsProbeControl = 0x01;
constexpr uint8_t kVsCommitControl = 0x02;

using Bytes = std::shared_ptr<std::vector<uint8_t>>;

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

struct QueuedFrame {
    Bytes data;
    double host_ms = 0;
    uint32_t pts = 0;
    uint64_t number = 0;
};

struct Camera {
    Stream stream;
    Format format;
    Frame frame;
    uint32_t interval = 0;
    size_t expected_size = 0;  // 0 for compressed frames
    size_t max_payload = 0;
    bool enabled = false;

    std::vector<libusb_transfer*> transfers;
    std::vector<std::vector<uint8_t>> buffers;
    int active_transfers = 0;

    Bytes assembling;
    bool have_frame = false;
    bool last_frame_id = false;
    bool frame_error = false;
    bool resyncing = false;
    uint32_t frame_pts = 0;

    std::mutex lock;
    std::condition_variable ready;
    std::deque<QueuedFrame> app_queue;
    std::deque<QueuedFrame> record_queue;
    bool recording = false;
    std::thread writer;
    FILE* data_file = nullptr;
    FILE* index_file = nullptr;

    uint64_t frames = 0;
    uint64_t dropped = 0;
    uint64_t app_skipped = 0;
    uint64_t record_lost = 0;
    uint64_t bad_headers = 0;
    uint64_t failed_transfers = 0;
};

struct Handle {
    libusb_context* context = nullptr;
    libusb_device_handle* device = nullptr;
    libusb_device* usb = nullptr;
    int speed = 0;
    std::vector<Stream> streams;
    Camera cameras[3];
    std::thread events;
    bool running = false;
    bool resubmit = false;
};

void start_frame(Camera& camera) {
    camera.assembling = std::make_shared<std::vector<uint8_t>>();
    camera.assembling->reserve(camera.expected_size ? camera.expected_size
                                                    : size_t(camera.frame.max_buffer));
}

void clear_frame(Camera& camera) {
    camera.have_frame = false;
    camera.frame_error = false;
    start_frame(camera);
}

void finish_frame(Camera& camera) {
    if (!camera.have_frame) return;
    const size_t size = camera.assembling->size();
    const bool bad_size = camera.expected_size ? size != camera.expected_size : size == 0;
    if (camera.frame_error || bad_size) {
        camera.dropped++;
        clear_frame(camera);
        return;
    }

    QueuedFrame frame{camera.assembling, now_ms(), camera.frame_pts, camera.frames++};
    {
        std::lock_guard<std::mutex> guard(camera.lock);
        if (camera.app_queue.size() >= kAppQueueFrames) {
            camera.app_queue.pop_front();
            camera.app_skipped++;
        }
        camera.app_queue.push_back(frame);
        if (camera.recording) {
            if (camera.record_queue.size() < kRecordQueueFrames) camera.record_queue.push_back(frame);
            else camera.record_lost++;
        }
    }
    camera.ready.notify_all();
    clear_frame(camera);
}

// Leftover data from an earlier stream can shift reads off the payload boundaries.
// A short transfer ends a payload, so the next one starts on a boundary again.
void handle_payload(Camera& camera, const uint8_t* data, size_t size) {
    if (size == 0) return;
    if (camera.resyncing) {
        if (size < camera.max_payload) camera.resyncing = false;
        return;
    }

    const PayloadHeader header = parse_payload_header(data, size);
    if (!header.valid) {
        camera.bad_headers++;
        if (camera.have_frame) camera.dropped++;
        clear_frame(camera);
        camera.resyncing = size == camera.max_payload;
        return;
    }

    if (camera.have_frame && header.frame_id != camera.last_frame_id) finish_frame(camera);
    camera.last_frame_id = header.frame_id;
    camera.have_frame = true;
    if (header.has_pts) camera.frame_pts = header.pts;
    if (header.error) camera.frame_error = true;
    camera.assembling->insert(camera.assembling->end(), data + header.length, data + size);
    if (header.end_of_frame) finish_frame(camera);
}

struct TransferContext {
    Handle* handle;
    Camera* camera;
};

void LIBUSB_CALL on_transfer(libusb_transfer* transfer) {
    auto* context = static_cast<TransferContext*>(transfer->user_data);
    Camera& camera = *context->camera;
    if (transfer->status == LIBUSB_TRANSFER_COMPLETED) {
        handle_payload(camera, transfer->buffer, transfer->actual_length);
    } else if (transfer->status != LIBUSB_TRANSFER_CANCELLED &&
               transfer->status != LIBUSB_TRANSFER_TIMED_OUT) {
        camera.failed_transfers++;
    }

    if (context->handle->resubmit && libusb_submit_transfer(transfer) == 0) return;
    camera.active_transfers--;
}

bool probe_and_commit(libusb_device_handle* device, Camera& camera) {
    uint8_t probe[kProbeSize] = {};
    probe[0] = 0x01;  // bmHint: keep dwFrameInterval fixed
    probe[2] = camera.format.index;
    probe[3] = camera.frame.index;
    write32(probe + 4, camera.interval);

    const uint16_t interface = camera.stream.streaming_interface;
    if (libusb_control_transfer(device, kClassInterfaceOut, kSetCur, kVsProbeControl << 8, interface,
                                probe, kProbeSize, 1000) != int(kProbeSize))
        return false;
    if (libusb_control_transfer(device, kClassInterfaceIn, kGetCur, kVsProbeControl << 8, interface,
                                probe, kProbeSize, 1000) != int(kProbeSize))
        return false;
    if (probe[2] != camera.format.index || probe[3] != camera.frame.index) return false;
    if (libusb_control_transfer(device, kClassInterfaceOut, kSetCur, kVsCommitControl << 8, interface,
                                probe, kProbeSize, 1000) != int(kProbeSize))
        return false;
    camera.max_payload = read32(probe + 22);
    return camera.max_payload > 0;
}

void writer_loop(Camera* camera) {
    while (true) {
        QueuedFrame frame;
        {
            std::unique_lock<std::mutex> guard(camera->lock);
            camera->ready.wait(guard, [&] { return !camera->record_queue.empty() || !camera->recording; });
            if (camera->record_queue.empty()) return;
            frame = camera->record_queue.front();
            camera->record_queue.pop_front();
        }
        fwrite(frame.data->data(), 1, frame.data->size(), camera->data_file);
        fprintf(camera->index_file, "%llu,%.3f,%zu,%u\n", (unsigned long long)frame.number,
                frame.host_ms, frame.data->size(), frame.pts);
    }
}

}  // namespace

extern "C" {

struct FsFrameInfo {
    double host_ms;
    uint32_t pts;
    uint32_t size;
    uint64_t number;
};

struct FsStats {
    uint64_t frames;
    uint64_t dropped;
    uint64_t app_skipped;
    uint64_t record_lost;
    uint64_t bad_headers;
    uint64_t failed_transfers;
};

void fs_close(void* opaque);

void* fs_open(int* speed) {
    auto handle = std::make_unique<Handle>();
    if (libusb_init(&handle->context) != 0) return nullptr;
    handle->device = libusb_open_device_with_vid_pid(handle->context, kVendorId, kProductId);
    if (!handle->device) {
        libusb_exit(handle->context);
        return nullptr;
    }
    handle->usb = libusb_get_device(handle->device);
    handle->speed = libusb_get_device_speed(handle->usb);
    handle->streams = read_streams(handle->usb);
    libusb_set_auto_detach_kernel_driver(handle->device, 1);
    if (speed) *speed = handle->speed;
    return handle.release();
}

// camera: 0 depth, 1 IR, 2 colour.
int fs_enable(void* opaque, int camera_index, const char* format_name, int width, int height, int fps) {
    auto* handle = static_cast<Handle*>(opaque);
    if (handle->running || camera_index < 0 || camera_index >= int(handle->streams.size())) return -1;
    Camera& camera = handle->cameras[camera_index];
    camera.stream = handle->streams[camera_index];
    for (const Format& format : camera.stream.formats) {
        if (format.name != format_name) continue;
        for (const Frame& frame : format.frames) {
            if (frame.width != width || frame.height != height) continue;
            for (uint32_t interval : frame.intervals) {
                if (uint32_t(10000000.0 / interval + 0.5) != uint32_t(fps)) continue;
                camera.format = format;
                camera.frame = frame;
                camera.interval = interval;
                camera.expected_size = format.bits_per_pixel
                                           ? size_t(width) * height * format.bits_per_pixel / 8
                                           : 0;
                camera.enabled = true;
                return 0;
            }
        }
    }
    return -2;
}

int fs_start(void* opaque) {
    auto* handle = static_cast<Handle*>(opaque);
    if (handle->running) return -1;

    for (Camera& camera : handle->cameras) {
        if (!camera.enabled) continue;
        if (libusb_claim_interface(handle->device, camera.stream.control_interface) != 0) return -2;
        if (libusb_claim_interface(handle->device, camera.stream.streaming_interface) != 0) return -3;
        if (!probe_and_commit(handle->device, camera)) return -4;
        start_frame(camera);
    }

    handle->running = true;
    handle->resubmit = true;
    handle->events = std::thread([handle] {
        while (handle->running) {
            timeval tv{0, 100000};
            libusb_handle_events_timeout_completed(handle->context, &tv, nullptr);
        }
    });

    static std::deque<TransferContext> contexts;
    contexts.clear();
    for (Camera& camera : handle->cameras) {
        if (!camera.enabled) continue;
        camera.buffers.assign(kTransfersPerStream, std::vector<uint8_t>(camera.max_payload));
        contexts.push_back({handle, &camera});
        for (int i = 0; i < kTransfersPerStream; i++) {
            libusb_transfer* transfer = libusb_alloc_transfer(0);
            libusb_fill_bulk_transfer(transfer, handle->device, camera.stream.endpoint,
                                      camera.buffers[i].data(), int(camera.max_payload), on_transfer,
                                      &contexts.back(), kTransferTimeoutMs);
            camera.transfers.push_back(transfer);
            camera.active_transfers++;
            if (libusb_submit_transfer(transfer) != 0) {
                camera.active_transfers--;
                return -5;
            }
        }
    }
    return 0;
}

// Returns the frame size, 0 on timeout, or a negative value if the buffer is too small.
int fs_read(void* opaque, int camera_index, uint8_t* out, size_t capacity, FsFrameInfo* info, int timeout_ms) {
    auto* handle = static_cast<Handle*>(opaque);
    Camera& camera = handle->cameras[camera_index];
    std::unique_lock<std::mutex> guard(camera.lock);
    if (!camera.ready.wait_for(guard, std::chrono::milliseconds(timeout_ms),
                               [&] { return !camera.app_queue.empty(); }))
        return 0;
    QueuedFrame frame = camera.app_queue.front();
    camera.app_queue.pop_front();
    guard.unlock();

    if (frame.data->size() > capacity) return -1;
    std::copy(frame.data->begin(), frame.data->end(), out);
    info->host_ms = frame.host_ms;
    info->pts = frame.pts;
    info->size = uint32_t(frame.data->size());
    info->number = frame.number;
    return int(frame.data->size());
}

int fs_record_start(void* opaque, int camera_index, const char* data_path, const char* index_path) {
    auto* handle = static_cast<Handle*>(opaque);
    Camera& camera = handle->cameras[camera_index];
    if (!camera.enabled || camera.recording) return -1;
    camera.data_file = fopen(data_path, "wb");
    camera.index_file = fopen(index_path, "w");
    if (!camera.data_file || !camera.index_file) return -2;
    fprintf(camera.index_file, "frame,host_ms,bytes,pts\n");
    {
        std::lock_guard<std::mutex> guard(camera.lock);
        camera.recording = true;
    }
    camera.writer = std::thread(writer_loop, &camera);
    return 0;
}

void fs_record_stop(void* opaque, int camera_index) {
    auto* handle = static_cast<Handle*>(opaque);
    Camera& camera = handle->cameras[camera_index];
    if (!camera.recording) return;
    {
        std::lock_guard<std::mutex> guard(camera.lock);
        camera.recording = false;
    }
    camera.ready.notify_all();
    camera.writer.join();
    fclose(camera.data_file);
    fclose(camera.index_file);
    camera.data_file = camera.index_file = nullptr;
}

void fs_stats(void* opaque, int camera_index, FsStats* stats) {
    auto* handle = static_cast<Handle*>(opaque);
    Camera& camera = handle->cameras[camera_index];
    std::lock_guard<std::mutex> guard(camera.lock);
    *stats = {camera.frames, camera.dropped, camera.app_skipped, camera.record_lost,
              camera.bad_headers, camera.failed_transfers};
}

void fs_stop(void* opaque) {
    auto* handle = static_cast<Handle*>(opaque);
    if (!handle->running) return;
    for (int i = 0; i < 3; i++) fs_record_stop(opaque, i);

    handle->resubmit = false;
    for (Camera& camera : handle->cameras)
        for (libusb_transfer* transfer : camera.transfers) libusb_cancel_transfer(transfer);
    for (int wait = 0; wait < 300; wait++) {
        int active = 0;
        for (Camera& camera : handle->cameras) active += camera.active_transfers;
        if (active == 0) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    handle->running = false;
    handle->events.join();

    for (Camera& camera : handle->cameras) {
        if (!camera.enabled) continue;
        for (libusb_transfer* transfer : camera.transfers) libusb_free_transfer(transfer);
        camera.transfers.clear();
        camera.buffers.clear();
        libusb_clear_halt(handle->device, camera.stream.endpoint);
        libusb_release_interface(handle->device, camera.stream.streaming_interface);
        libusb_release_interface(handle->device, camera.stream.control_interface);
        camera.enabled = false;
        std::lock_guard<std::mutex> guard(camera.lock);
        camera.app_queue.clear();
    }
}

// Reads the current depth mode name. Streams must be stopped. Returns 0 on success.
int fs_get_depth_mode(void* opaque, char* out, size_t capacity) {
    auto* handle = static_cast<Handle*>(opaque);
    if (handle->running) return -1;
    if (libusb_claim_interface(handle->device, kControlInterface) != 0) return -2;
    Scanner scanner(handle->device);
    const Response current = scanner.transact(kOpGetStruct, {kCurrentModeStruct}, 512);
    libusb_release_interface(handle->device, kControlInterface);
    if (!current.ok || current.data.size() < kModeRecordSize) return -3;
    snprintf(out, capacity, "%s", mode_name(current.data.data()).c_str());
    return 0;
}

// Switches to one of the two default depth modes. Streams must be stopped.
int fs_set_depth_mode(void* opaque, const char* target) {
    auto* handle = static_cast<Handle*>(opaque);
    const std::string name = target;
    bool allowed = false;
    for (const char* mode : kAllowedModes) allowed = allowed || name == mode;
    if (handle->running || !allowed) return -1;
    if (libusb_claim_interface(handle->device, kControlInterface) != 0) return -2;

    int status = -3;
    Scanner scanner(handle->device);
    std::vector<uint8_t> list;
    if (read_mode_list(scanner, &list)) {
        for (size_t i = 0; i < list.size(); i += kModeRecordSize) {
            if (mode_name(list.data() + i) != name) continue;
            std::vector<uint8_t> payload(6 + kModeRecordSize);
            write32(payload.data(), kCurrentModeStruct);
            write16(payload.data() + 4, 0);
            std::copy(list.begin() + i, list.begin() + i + kModeRecordSize, payload.begin() + 6);
            const uint16_t id = scanner.next_id();
            status = scanner.send_once(build_request(kOpSetStructV11, id, payload), id).ok ? 0 : -4;
            break;
        }
    }
    libusb_release_interface(handle->device, kControlInterface);
    return status;
}

void fs_close(void* opaque) {
    auto* handle = static_cast<Handle*>(opaque);
    if (!handle) return;
    fs_stop(opaque);
    libusb_close(handle->device);
    libusb_exit(handle->context);
    delete handle;
}

}  // extern "C"
