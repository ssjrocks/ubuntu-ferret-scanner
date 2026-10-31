# Scanner commands

Source: Orbbec SDK v2 (github.com/orbbec/OrbbecSDK_v2, core code under the MIT
licence) and read-only tests on a Ferret Pro with `ferret-props`. Date: 2026-10-31.

The Orbbec SDK's USB rules list `2bc5:069c` as `TB2201`. No other file in the SDK
names that device, so the SDK does not claim to support it.

## Transport

- Commands use the depth camera's extension unit: unit 4, interface 0, GUID
  `a55751a1-f3c5-4a5e-8d5a-6854b8fa2716`.
- A request is sent with `SET_CUR`, and the reply is read with `GET_CUR`.
- Control 2 carries 64-byte messages. Controls 1 and 3 carry 512 and 1024 bytes.

## Messages

All fields are little-endian.

Request header: magic `0x4d47`, payload size in 16-bit words, opcode, request ID.

Response header: magic `0x4252`, size, opcode, request ID, error code.

| Opcode | Meaning |
|---|---|
| 1 | Get property |
| 2 | Set property |
| 3 | Get structure |
| 4 | Set structure |

Get property sends a 32-bit property ID. The reply holds five 32-bit values: current,
maximum, minimum, default and step.

Error `0x0002` means the scanner does not support the request.

## Test results

Of 130 properties documented in the SDK, 32 answered. Only get requests were sent.

| ID | SDK name | Current | Default | Note |
|---|---|---|---|---|
| 2 | LDP | 0 | 0 | |
| 3 | Laser | 0 | 0 | Read while not streaming |
| 4 | Laser pulse width | 2065 | 2200 | Range 100-6800, differs from default |
| 18 | IR mirror | 1 | 0 | Matches the mirrored IR image |
| 14, 81 | Depth, colour mirror | 0 | 0 | Matches unmirrored images |
| 75 | Depth precision level | 1 | 0 | Range 0-1, differs from default |
| 85 | Disparity to depth | 0 | 0 | Fits disparity-like values, unproven |
| 100 | LDP measure distance | 0 | 0 | Range 0-3000 |

- The mirror results match what the cameras showed, so the transport is working.
- Disparity search range and offset (191, 196, 271, 272) and min and max depth (22,
  23) are not supported, so they do not explain the 32 cm limit.
- Some replies are not reliable under the SDK's meanings. IDs 7, 99 and 119 returned
  identical values that make no sense as stated, and IDs 5, 25 and 26 look like
  floating-point data. This firmware may reuse some IDs.

## Unknowns

- What sets the depth range. The official app reaches past 40 cm.
- Whether settings survive a power cycle. The two non-default values may have been
  left by the official app or set at the factory.
- Structure reads, including depth modes and calibration, are untested.

## Device data

Date: 2026-10-31. Source: read-only structure and raw-data requests.

- Chip `MX6600`, firmware `1.2.2`. The SDK says MX6600 raw disparity is 14 bits, which
  matches Y14.
- Struct 1043 (current depth mode) reads `Close_range_mode Default`.
- Raw 4034 (depth mode list) holds 52-byte records. Mode names start 16 bytes in:

| Mode |
|---|
| `Close_range_mode Default` (current) |
| `Long_range_mode Default` |
| `Close_range_mode Interleave` |
| `Long_range_mode Interleave` |
| `Close_range_mode Narrow_View` |
| `Long_range_mode Narrow_View` |
| `Grid_of_Dots Calibration` |
| `In_scene Calibration` |

- `Long_range_mode` may explain why the official app reaches past 40 cm. This is
  unproven until the mode is set and tested.
- Struct 1002 (baseline calibration) holds floats 130.0 and 7.5. Their meaning and
  order are unconfirmed.
- Raw 4026 (depth calibration) is 28 bytes and is not decoded.
- Struct 1001 (camera parameters) is all NaN, so lens calibration is not there.
- Struct 1003 (temperatures) reads about 28 C.
- Structs 1045, 1064 and 1103 are not supported.

## Do not read

Raw block 4027 stopped the scanner responding. Its depth control channel failed with
USB error -71. A USB reset then dropped the scanner from the bus until it was replugged.
`ferret-props` refuses this block and stops at the first failed request.

Read unknown blocks one at a time, and check the camera nodes after each.

Device data contains the serial number and calibration, so keep it out of this repo.
