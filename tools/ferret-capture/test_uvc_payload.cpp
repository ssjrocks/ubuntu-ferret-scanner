#include <cassert>

#include "uvc_payload.h"

int main() {
    const uint8_t with_pts[] = {12, 0x87, 0x78, 0x56, 0x34, 0x12, 0, 0, 0, 0, 0, 0, 0xAA};
    PayloadHeader header = parse_payload_header(with_pts, sizeof with_pts);
    assert(header.valid);
    assert(header.length == 12);
    assert(header.frame_id);
    assert(header.end_of_frame);
    assert(header.has_pts);
    assert(!header.error);
    assert(header.pts == 0x12345678);

    const uint8_t no_end_of_header[] = {2, 0x40};
    assert(!parse_payload_header(no_end_of_header, sizeof no_end_of_header).valid);

    const uint8_t minimal[] = {2, 0xC0};
    header = parse_payload_header(minimal, sizeof minimal);
    assert(header.valid);
    assert(header.error);
    assert(!header.frame_id);
    assert(!header.has_pts);

    const uint8_t too_short[] = {2};
    assert(!parse_payload_header(too_short, sizeof too_short).valid);

    const uint8_t longer_than_data[] = {12, 0x80, 0};
    assert(!parse_payload_header(longer_than_data, sizeof longer_than_data).valid);

    const uint8_t zero_length[] = {0, 0x80};
    assert(!parse_payload_header(zero_length, sizeof zero_length).valid);

    const uint8_t pts_without_room[] = {2, 0x84, 0, 0, 0, 0};
    assert(!parse_payload_header(pts_without_room, sizeof pts_without_room).valid);
    return 0;
}
