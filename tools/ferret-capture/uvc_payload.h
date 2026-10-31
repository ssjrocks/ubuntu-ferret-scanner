// UVC bulk payload header parsing, from the USB Video Class 1.1 specification.
#pragma once

#include <cstddef>
#include <cstdint>

struct PayloadHeader {
    bool valid = false;
    size_t length = 0;
    bool frame_id = false;
    bool end_of_frame = false;
    bool has_pts = false;
    bool error = false;
    uint32_t pts = 0;
};

inline PayloadHeader parse_payload_header(const uint8_t* data, size_t size) {
    PayloadHeader header;
    if (size < 2 || data[0] < 2 || data[0] > size) return header;

    const uint8_t info = data[1];
    header.length = data[0];
    header.frame_id = info & 0x01;
    header.end_of_frame = info & 0x02;
    header.has_pts = info & 0x04;
    header.error = info & 0x40;

    if (header.has_pts) {
        if (header.length < 6) return header;
        header.pts = uint32_t(data[2]) | uint32_t(data[3]) << 8 | uint32_t(data[4]) << 16 |
                     uint32_t(data[5]) << 24;
    }
    header.valid = true;
    return header;
}
