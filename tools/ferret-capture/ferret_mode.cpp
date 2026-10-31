// Shows the scanner's depth modes and switches between the two default modes.
//
// A switch sends back a record the scanner itself listed, once, with no stream running.
// Without --write it only prints the bytes it would send.
#include <cstring>

#include "depth_modes.h"
#include "scanner_link.h"

namespace {

void print_bytes(const std::vector<uint8_t>& bytes, size_t count) {
    for (size_t i = 0; i < count; i++) {
        printf("%02x", bytes[i]);
        printf((i + 1) % 16 == 0 ? "\n" : " ");
    }
    if (count % 16 != 0) printf("\n");
}

int run(Scanner& scanner, const std::string& target, bool write) {
    const Response version = scanner.transact(kOpGetCommandVersion, {kCurrentModeStruct}, 64);
    uint16_t command_version = 0;
    if (version.ok && version.data.size() >= 2) {
        command_version = read16(version.data.data());
        printf("command version: %u\n", command_version);
    } else {
        printf("command version: unavailable (0x%04x)\n", version.error);
    }

    const Response current = scanner.transact(kOpGetStruct, {kCurrentModeStruct}, 512);
    if (!current.ok || current.data.size() < kModeRecordSize) {
        fprintf(stderr, "cannot read the current mode\n");
        return 1;
    }
    const std::string current_name = mode_name(current.data.data());
    printf("current mode: %s\n", current_name.c_str());

    const Response versioned = scanner.transact(kOpGetStructV11, {kCurrentModeStruct}, 512);
    if (versioned.ok && versioned.data.size() >= 2 + kModeRecordSize) {
        printf("versioned read: version %u, %s\n", read16(versioned.data.data()),
               mode_name(versioned.data.data() + 2).c_str());
    } else {
        printf("versioned read: unavailable (0x%04x)\n", versioned.error);
    }

    std::vector<uint8_t> list;
    if (!read_mode_list(scanner, &list)) {
        fprintf(stderr, "cannot read the mode list\n");
        return 1;
    }
    const uint8_t* target_record = nullptr;
    for (size_t i = 0; i < list.size(); i += kModeRecordSize) {
        const std::string name = mode_name(list.data() + i);
        printf("mode %zu: %s\n", i / kModeRecordSize, name.c_str());
        if (name == target) target_record = list.data() + i;
    }
    if (target.empty()) return 0;

    bool allowed = false;
    for (const char* mode : kAllowedModes) allowed = allowed || target == mode;
    if (!allowed || !target_record) {
        fprintf(stderr, "refusing mode: %s\n", target.c_str());
        return 1;
    }
    if (target == current_name) {
        printf("already in %s\n", target.c_str());
        return 0;
    }

    std::vector<uint8_t> payload(6 + kModeRecordSize);
    write32(payload.data(), kCurrentModeStruct);
    write16(payload.data() + 4, command_version);
    std::copy(target_record, target_record + kModeRecordSize, payload.begin() + 6);

    const uint16_t id = scanner.next_id();
    const std::vector<uint8_t> request = build_request(kOpSetStructV11, id, payload);
    printf("request: %zu bytes on control %u, meaningful bytes:\n", request.size(), kControl512Bytes);
    print_bytes(request, 8 + payload.size());
    if (!write) {
        printf("dry run, nothing sent\n");
        return 0;
    }

    const Response response = scanner.send_once(request, id);
    if (response.ok) {
        printf("scanner accepted the switch\n");
        return 0;
    }
    if (response.replied) {
        fprintf(stderr, "scanner refused the switch (0x%04x)\n", response.error);
        return 1;
    }
    printf("no reply, the scanner may be restarting\n");
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::string target;
    bool write = false;
    for (int i = 1; i < argc; i++) {
        const std::string arg = argv[i];
        if (arg == "--set" && i + 1 < argc) {
            target = argv[++i];
        } else if (arg == "--write") {
            write = true;
        } else {
            fprintf(stderr, "usage: ferret-mode [--set MODE [--write]]\n");
            return 2;
        }
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
    const int status = run(scanner, target, write);

    libusb_release_interface(handle, kControlInterface);
    libusb_close(handle);
    libusb_exit(context);
    return status;
}
