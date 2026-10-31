// Reads scanner properties and data blocks over the depth camera's extension unit.
//
// The request format follows Orbbec's MIT-licensed SDK (see docs/protocol/commands.md).
// This tool sends only get and read requests, so it cannot change a setting.
#include <libusb.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr uint16_t kVendorId = 0x2bc5;
constexpr uint16_t kProductId = 0x069c;
constexpr uint8_t kControlInterface = 0;
constexpr uint8_t kExtensionUnit = 4;
constexpr unsigned kTimeoutMs = 1000;
constexpr int kAttempts = 3;

constexpr uint8_t kControl512Bytes = 1;
constexpr uint8_t kControl64Bytes = 2;
constexpr uint8_t kControl1024Bytes = 3;
constexpr uint16_t kRequestSize = 64;

constexpr uint8_t kSetCur = 0x01;
constexpr uint8_t kGetCur = 0x81;
constexpr uint8_t kClassInterfaceOut = 0x21;
constexpr uint8_t kClassInterfaceIn = 0xA1;

constexpr uint16_t kRequestMagic = 0x4d47;
constexpr uint16_t kResponseMagic = 0x4252;
constexpr size_t kResponseHeaderSize = 10;
constexpr uint32_t kChunkSize = 768;

constexpr uint16_t kOpGetProperty = 1;
constexpr uint16_t kOpGetStruct = 3;
constexpr uint16_t kOpRawStart = 17;
constexpr uint16_t kOpRawRead = 18;
constexpr uint16_t kOpRawFinish = 19;
constexpr uint16_t kOpListStart = 29;
constexpr uint16_t kOpListRead = 30;
constexpr uint16_t kOpListFinish = 31;

constexpr uint16_t kBusyError = 0x0003;
constexpr uint32_t kHangingRawBlock = 4027;

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

uint32_t read32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

struct Response {
    bool ok = false;
    uint16_t error = 0;
    std::vector<uint8_t> data;  // everything after the header
};

class Scanner {
public:
    explicit Scanner(libusb_device_handle* handle) : handle_(handle) {}

    Response transact(uint16_t opcode, const std::vector<uint32_t>& arguments, uint16_t reply_size) {
        const uint16_t id = next_id_++;
        uint8_t request[kRequestSize] = {};
        write16(request, kRequestMagic);
        write16(request + 2, uint16_t(arguments.size() * 2));
        write16(request + 4, opcode);
        write16(request + 6, id);
        for (size_t i = 0; i < arguments.size(); i++) write32(request + 8 + 4 * i, arguments[i]);

        uint8_t reply_control = kControl64Bytes;
        if (reply_size > 512) reply_control = kControl1024Bytes;
        else if (reply_size > 64) reply_control = kControl512Bytes;

        Response response;
        for (int attempt = 0; attempt < kAttempts; attempt++) {
            if (attempt > 0) std::this_thread::sleep_for(std::chrono::milliseconds(100));

            int result = libusb_control_transfer(handle_, kClassInterfaceOut, kSetCur,
                                                 uint16_t(kControl64Bytes) << 8, index(), request,
                                                 kRequestSize, kTimeoutMs);
            if (result != kRequestSize) continue;

            std::vector<uint8_t> reply(reply_size);
            result = libusb_control_transfer(handle_, kClassInterfaceIn, kGetCur,
                                             uint16_t(reply_control) << 8, index(), reply.data(),
                                             reply_size, kTimeoutMs);
            if (result < int(kResponseHeaderSize)) continue;
            if (read16(reply.data()) != kResponseMagic || read16(reply.data() + 6) != id) continue;

            response.error = read16(reply.data() + 8);
            if (response.error == kBusyError) continue;
            response.ok = response.error == 0;
            if (response.ok) {
                const size_t size = size_t(read16(reply.data() + 2)) * 2 + 8;
                const size_t end = std::min<size_t>(size, size_t(result));
                if (end > kResponseHeaderSize)
                    response.data.assign(reply.begin() + kResponseHeaderSize, reply.begin() + end);
            }
            return response;
        }
        failed_ = true;
        return response;
    }

    bool failed() const { return failed_; }

private:
    uint16_t index() const { return uint16_t(kExtensionUnit) << 8 | kControlInterface; }

    libusb_device_handle* handle_;
    uint16_t next_id_ = 1;
    bool failed_ = false;
};

void print_property(Scanner& scanner, const Property& property) {
    const Response response = scanner.transact(kOpGetProperty, {property.id}, 64);
    if (!response.ok || response.data.size() < 20) {
        printf("%-36s %4u: unavailable (0x%04x)\n", property.name, property.id, response.error);
        return;
    }
    const uint8_t* d = response.data.data();
    printf("%-36s %4u: current %d, max %d, min %d, default %d, step %d\n", property.name,
           property.id, int32_t(read32(d)), int32_t(read32(d + 4)), int32_t(read32(d + 8)),
           int32_t(read32(d + 12)), int32_t(read32(d + 16)));
}

bool save(const std::string& directory, const char* kind, uint32_t id,
          const std::vector<uint8_t>& data) {
    const std::string path = directory + "/" + kind + "_" + std::to_string(id) + ".bin";
    FILE* file = fopen(path.c_str(), "wb");
    if (!file) {
        fprintf(stderr, "cannot write %s\n", path.c_str());
        return false;
    }
    fwrite(data.data(), 1, data.size(), file);
    fclose(file);
    printf("%s %u: %zu bytes, saved to %s\n", kind, id, data.size(), path.c_str());
    return true;
}

void read_struct(Scanner& scanner, uint32_t id, const std::string& directory) {
    const Response response = scanner.transact(kOpGetStruct, {id}, 512);
    if (!response.ok) {
        printf("struct %u: unavailable (0x%04x)\n", id, response.error);
        return;
    }
    save(directory, "struct", id, response.data);
}

// Raw data and list reads share one shape: start, read in chunks, finish.
void read_block(Scanner& scanner, const char* kind, uint32_t id, uint16_t start_op, uint16_t read_op,
                uint16_t finish_op, size_t size_offset, size_t data_offset,
                const std::string& directory) {
    if (id == kHangingRawBlock) {
        printf("%s %u: skipped, this read stopped the scanner responding\n", kind, id);
        return;
    }

    const Response start = scanner.transact(start_op, {id}, 64);
    if (!start.ok || start.data.size() < size_offset + 4) {
        printf("%s %u: unavailable (0x%04x)\n", kind, id, start.error);
        return;
    }

    const uint32_t size = read32(start.data.data() + size_offset);
    std::vector<uint8_t> data;
    bool complete = true;
    for (uint32_t offset = 0; offset < size; offset += kChunkSize) {
        const uint32_t chunk = std::min(kChunkSize, size - offset);
        const Response part = scanner.transact(read_op, {id, offset, chunk}, 1024);
        if (!part.ok || part.data.size() < data_offset + chunk) {
            printf("%s %u: read failed at offset %u (0x%04x)\n", kind, id, offset, part.error);
            complete = false;
            break;
        }
        data.insert(data.end(), part.data.begin() + data_offset,
                    part.data.begin() + data_offset + chunk);
    }
    scanner.transact(finish_op, {id}, 64);
    if (complete) save(directory, kind, id, data);
}

void usage() {
    fprintf(stderr,
            "usage: ferret-props [PROPERTY_ID...]\n"
            "       ferret-props --out DIR [--struct ID] [--raw ID] [--list ID] ...\n");
}

}  // namespace

int main(int argc, char** argv) {
    std::string directory;
    std::vector<uint32_t> properties, structs, raws, lists;
    for (int i = 1; i < argc; i++) {
        const std::string arg = argv[i];
        if (arg == "--out" && i + 1 < argc) directory = argv[++i];
        else if (arg == "--struct" && i + 1 < argc) structs.push_back(atoi(argv[++i]));
        else if (arg == "--raw" && i + 1 < argc) raws.push_back(atoi(argv[++i]));
        else if (arg == "--list" && i + 1 < argc) lists.push_back(atoi(argv[++i]));
        else if (arg[0] != '-') properties.push_back(atoi(arg.c_str()));
        else {
            usage();
            return 2;
        }
    }
    if (directory.empty() && !(structs.empty() && raws.empty() && lists.empty())) {
        usage();
        return 2;
    }

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

    Scanner scanner(handle);
    if (argc == 1) {
        for (const Property& property : kProperties) print_property(scanner, property);
    }
    // Stop at the first failure: more requests can make a stuck scanner worse.
    for (uint32_t id : properties) {
        if (!scanner.failed()) print_property(scanner, {id, "property"});
    }
    for (uint32_t id : structs) {
        if (!scanner.failed()) read_struct(scanner, id, directory);
    }
    for (uint32_t id : raws) {
        if (!scanner.failed())
            read_block(scanner, "raw", id, kOpRawStart, kOpRawRead, kOpRawFinish, 0, 0, directory);
    }
    for (uint32_t id : lists) {
        if (!scanner.failed())
            read_block(scanner, "list", id, kOpListStart, kOpListRead, kOpListFinish, 2, 0, directory);
    }
    if (scanner.failed()) fprintf(stderr, "scanner stopped responding, stopping here\n");

    libusb_release_interface(handle, kControlInterface);
    libusb_close(handle);
    libusb_exit(context);
    return 0;
}
