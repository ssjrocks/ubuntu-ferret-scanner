// Depth mode records, read from and written back to the scanner.
#pragma once

#include <cstring>
#include <string>
#include <vector>

#include "scanner_link.h"

constexpr uint32_t kCurrentModeStruct = 1043;
constexpr uint32_t kModeListBlock = 4034;
constexpr size_t kModeRecordSize = 52;
constexpr size_t kModeNameOffset = 16;
constexpr size_t kModeNameSize = 32;
constexpr const char* kAllowedModes[] = {"Close_range_mode Default", "Long_range_mode Default"};

inline std::string mode_name(const uint8_t* record) {
    const char* name = reinterpret_cast<const char*>(record + kModeNameOffset);
    return std::string(name, strnlen(name, kModeNameSize));
}

inline bool read_mode_list(Scanner& scanner, std::vector<uint8_t>* out) {
    const Response start = scanner.transact(kOpRawStart, {kModeListBlock}, 64);
    if (!start.ok || start.data.size() < 4) return false;

    const uint32_t size = read32(start.data.data());
    bool complete = true;
    for (uint32_t offset = 0; offset < size; offset += kChunkSize) {
        const uint32_t chunk = std::min(kChunkSize, size - offset);
        const Response part = scanner.transact(kOpRawRead, {kModeListBlock, offset, chunk}, 1024);
        if (!part.ok || part.data.size() < chunk) {
            complete = false;
            break;
        }
        out->insert(out->end(), part.data.begin(), part.data.begin() + chunk);
    }
    scanner.transact(kOpRawFinish, {kModeListBlock}, 64);
    return complete && !out->empty() && out->size() % kModeRecordSize == 0;
}
