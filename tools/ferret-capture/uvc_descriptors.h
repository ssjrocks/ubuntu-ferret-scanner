// UVC streaming descriptors for the scanner's depth, IR and colour cameras.
#pragma once

#include <libusb.h>

#include <cstdint>
#include <string>
#include <vector>

constexpr uint8_t kCsInterface = 0x24;
constexpr uint8_t kVsFormatUncompressed = 0x04;
constexpr uint8_t kVsFrameUncompressed = 0x05;
constexpr uint8_t kVsFormatMjpeg = 0x06;
constexpr uint8_t kVsFrameMjpeg = 0x07;

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

inline uint16_t uvc_read16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }

inline uint32_t uvc_read32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

inline std::string fourcc_name(const uint8_t* guid) {
    std::string name(reinterpret_cast<const char*>(guid), 4);
    while (!name.empty() && (name.back() == ' ' || name.back() == '\0')) name.pop_back();
    return name;
}

// Frame descriptors keep the same layout for uncompressed and MJPEG formats.
inline Frame parse_frame(const uint8_t* d, size_t length) {
    Frame frame;
    if (length < 26) return frame;
    frame.index = d[3];
    frame.width = uvc_read16(d + 5);
    frame.height = uvc_read16(d + 7);
    frame.max_buffer = uvc_read32(d + 17);
    const uint8_t interval_type = d[25];
    for (size_t i = 0; i < interval_type && 26 + 4 * i + 4 <= length; i++)
        frame.intervals.push_back(uvc_read32(d + 26 + 4 * i));
    return frame;
}

inline std::vector<Format> parse_formats(const uint8_t* extra, int extra_length) {
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

inline std::vector<Stream> read_streams(libusb_device* device) {
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
