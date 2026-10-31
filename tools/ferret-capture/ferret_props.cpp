// Reads scanner properties over the depth camera's extension unit.
//
// The request format follows Orbbec's MIT-licensed SDK (see docs/protocol/commands.md).
// This tool sends get-property requests only, so it cannot change a setting.
#include <libusb.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

constexpr uint16_t kVendorId = 0x2bc5;
constexpr uint16_t kProductId = 0x069c;
constexpr uint8_t kControlInterface = 0;
constexpr uint8_t kExtensionUnit = 4;
constexpr uint8_t kControl64Bytes = 2;
constexpr uint16_t kMessageSize = 64;
constexpr unsigned kTimeoutMs = 1000;

constexpr uint8_t kSetCur = 0x01;
constexpr uint8_t kGetCur = 0x81;
constexpr uint8_t kClassInterfaceOut = 0x21;
constexpr uint8_t kClassInterfaceIn = 0xA1;

constexpr uint16_t kRequestMagic = 0x4d47;
constexpr uint16_t kResponseMagic = 0x4252;
constexpr uint16_t kOpcodeGetProperty = 1;
constexpr size_t kResponseHeaderSize = 10;

struct Property {
    uint32_t id;
    const char* name;
};

constexpr Property kProperties[] = {
    {3, "laser on"},
    {6, "flood on"},
    {22, "min depth"},
    {23, "max depth"},
    {75, "depth precision level"},
    {79, "laser mode"},
    {85, "disparity to depth"},
    {99, "laser power level"},
    {191, "disparity search range mode"},
    {196, "disparity search offset"},
    {271, "current disparity search range mode"},
    {272, "current disparity search offset"},
};

void write16(uint8_t* p, uint16_t value) {
    p[0] = uint8_t(value);
    p[1] = uint8_t(value >> 8);
}

void write32(uint8_t* p, uint32_t value) {
    for (int i = 0; i < 4; i++) p[i] = uint8_t(value >> (8 * i));
}

uint16_t read16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }

int32_t read32(const uint8_t* p) {
    return int32_t(uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 |
                   uint32_t(p[3]) << 24);
}

void get_property(libusb_device_handle* handle, const Property& property, uint16_t request_id) {
    uint8_t request[kMessageSize] = {};
    write16(request, kRequestMagic);
    write16(request + 2, 2);  // payload size in 16-bit words
    write16(request + 4, kOpcodeGetProperty);
    write16(request + 6, request_id);
    write32(request + 8, property.id);

    const uint16_t value = uint16_t(kControl64Bytes) << 8;
    const uint16_t index = uint16_t(kExtensionUnit) << 8 | kControlInterface;
    int result = libusb_control_transfer(handle, kClassInterfaceOut, kSetCur, value, index, request,
                                         kMessageSize, kTimeoutMs);
    if (result != kMessageSize) {
        printf("%-36s %3u: send failed: %s\n", property.name, property.id,
               libusb_error_name(result));
        return;
    }

    uint8_t response[kMessageSize] = {};
    result = libusb_control_transfer(handle, kClassInterfaceIn, kGetCur, value, index, response,
                                     kMessageSize, kTimeoutMs);
    if (result < int(kResponseHeaderSize)) {
        printf("%-36s %3u: receive failed: %s\n", property.name, property.id,
               libusb_error_name(result));
        return;
    }
    if (read16(response) != kResponseMagic || read16(response + 6) != request_id) {
        printf("%-36s %3u: unexpected response\n", property.name, property.id);
        return;
    }

    const uint16_t error = read16(response + 8);
    if (error != 0) {
        printf("%-36s %3u: not supported or refused (0x%04x)\n", property.name, property.id, error);
        return;
    }

    const uint8_t* data = response + kResponseHeaderSize;
    printf("%-36s %3u: current %d, max %d, min %d, default %d, step %d\n", property.name,
           property.id, read32(data), read32(data + 4), read32(data + 8), read32(data + 12),
           read32(data + 16));
}

}  // namespace

int main(int argc, char** argv) {
    libusb_context* context = nullptr;
    int result = libusb_init(&context);
    if (result != 0) {
        fprintf(stderr, "libusb_init failed: %s\n", libusb_error_name(result));
        return 1;
    }

    libusb_device_handle* handle = libusb_open_device_with_vid_pid(context, kVendorId, kProductId);
    if (!handle) {
        fprintf(stderr, "scanner not found or not accessible\n");
        libusb_exit(context);
        return 1;
    }

    // Auto-detach returns the interface to uvcvideo when it is released.
    libusb_set_auto_detach_kernel_driver(handle, 1);
    result = libusb_claim_interface(handle, kControlInterface);
    if (result != 0) {
        fprintf(stderr, "cannot claim control interface: %s\n", libusb_error_name(result));
        libusb_close(handle);
        libusb_exit(context);
        return 1;
    }

    // Extra arguments are property IDs to read instead of the default list.
    uint16_t request_id = 1;
    if (argc > 1) {
        for (int i = 1; i < argc; i++)
            get_property(handle, {uint32_t(atoi(argv[i])), "property"}, request_id++);
    } else {
        for (const Property& property : kProperties) get_property(handle, property, request_id++);
    }

    libusb_release_interface(handle, kControlInterface);
    libusb_close(handle);
    libusb_exit(context);
    return 0;
}
