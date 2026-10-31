// Command transport over the depth camera's extension unit.
//
// The request format follows Orbbec's MIT-licensed SDK (see docs/protocol/commands.md).
#pragma once

#include <libusb.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

constexpr uint16_t kVendorId = 0x2bc5;
constexpr uint16_t kProductId = 0x069c;
constexpr uint8_t kControlInterface = 0;
constexpr uint8_t kExtensionUnit = 4;
constexpr unsigned kTimeoutMs = 1000;
constexpr int kAttempts = 3;

constexpr uint8_t kControl512Bytes = 1;
constexpr uint8_t kControl64Bytes = 2;
constexpr uint8_t kControl1024Bytes = 3;

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
constexpr uint16_t kOpGetCommandVersion = 26;
constexpr uint16_t kOpGetStructV11 = 27;
constexpr uint16_t kOpSetStructV11 = 28;
constexpr uint16_t kOpListStart = 29;
constexpr uint16_t kOpListRead = 30;
constexpr uint16_t kOpListFinish = 31;

constexpr uint16_t kBusyError = 0x0003;
constexpr uint32_t kHangingRawBlock = 4027;

inline void write16(uint8_t* p, uint16_t value) {
    p[0] = uint8_t(value);
    p[1] = uint8_t(value >> 8);
}

inline void write32(uint8_t* p, uint32_t value) {
    for (int i = 0; i < 4; i++) p[i] = uint8_t(value >> (8 * i));
}

inline uint16_t read16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }

inline uint32_t read32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

struct Response {
    bool ok = false;
    bool replied = false;
    uint16_t error = 0;
    std::vector<uint8_t> data;  // everything after the header
};

// Builds a request, padded to the 64, 512 or 1024 bytes the extension unit expects.
inline std::vector<uint8_t> build_request(uint16_t opcode, uint16_t id,
                                          const std::vector<uint8_t>& payload) {
    const size_t needed = 8 + payload.size();
    std::vector<uint8_t> request(needed <= 64 ? 64 : needed <= 512 ? 512 : 1024);
    write16(request.data(), kRequestMagic);
    write16(request.data() + 2, uint16_t((payload.size() + 1) / 2));
    write16(request.data() + 4, opcode);
    write16(request.data() + 6, id);
    std::copy(payload.begin(), payload.end(), request.begin() + 8);
    return request;
}

class Scanner {
public:
    explicit Scanner(libusb_device_handle* handle) : handle_(handle) {}

    Response transact(uint16_t opcode, const std::vector<uint32_t>& arguments, uint16_t reply_size) {
        std::vector<uint8_t> payload(arguments.size() * 4);
        for (size_t i = 0; i < arguments.size(); i++) write32(payload.data() + 4 * i, arguments[i]);
        const uint16_t id = next_id_++;
        return exchange(build_request(opcode, id, payload), id, reply_size, kAttempts);
    }

    // Writes are sent once: repeating one could race the scanner restarting.
    Response send_once(const std::vector<uint8_t>& request, uint16_t id) {
        return exchange(request, id, 64, 1);
    }

    uint16_t next_id() { return next_id_++; }
    bool failed() const { return failed_; }

private:
    Response exchange(const std::vector<uint8_t>& request, uint16_t id, uint16_t reply_size,
                      int attempts) {
        uint8_t request_control = kControl64Bytes;
        if (request.size() > 512) request_control = kControl1024Bytes;
        else if (request.size() > 64) request_control = kControl512Bytes;

        uint8_t reply_control = kControl64Bytes;
        if (reply_size > 512) reply_control = kControl1024Bytes;
        else if (reply_size > 64) reply_control = kControl512Bytes;

        Response response;
        std::vector<uint8_t> sent = request;
        for (int attempt = 0; attempt < attempts; attempt++) {
            if (attempt > 0) std::this_thread::sleep_for(std::chrono::milliseconds(100));

            int result = libusb_control_transfer(handle_, kClassInterfaceOut, kSetCur,
                                                 uint16_t(request_control) << 8, index(), sent.data(),
                                                 uint16_t(sent.size()), kTimeoutMs);
            if (result != int(sent.size())) continue;

            std::vector<uint8_t> reply(reply_size);
            result = libusb_control_transfer(handle_, kClassInterfaceIn, kGetCur,
                                             uint16_t(reply_control) << 8, index(), reply.data(),
                                             reply_size, kTimeoutMs);
            if (result < int(kResponseHeaderSize)) continue;
            if (read16(reply.data()) != kResponseMagic || read16(reply.data() + 6) != id) continue;

            response.replied = true;
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

    uint16_t index() const { return uint16_t(kExtensionUnit) << 8 | kControlInterface; }

    libusb_device_handle* handle_;
    uint16_t next_id_ = 1;
    bool failed_ = false;
};
