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
