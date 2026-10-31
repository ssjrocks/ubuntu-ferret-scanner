// Reads scanner properties and data blocks over the depth camera's extension unit.
//
// The request format follows Orbbec's MIT-licensed SDK (see docs/protocol/commands.md).
// This tool sends only get and read requests, so it cannot change a setting.
#include <cstdlib>
#include <cstring>

#include "scanner_link.h"

namespace {

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
    if (id == kHangingRawBlock && start_op == kOpRawStart) {
        printf("%s %u: skipped, a raw read stopped the scanner responding. Use --list.\n", kind, id);
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
