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

- Whether long-range mode matches the official app's range.
- Whether settings survive a power cycle. The two non-default values may have been
  left by the official app or set at the factory.
- Where the lens and alignment calibration can be read safely.

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
After the replug, colour and depth Y14 streamed at 30 fps with no USB errors.
`ferret-props` refuses this block and stops at the first failed request.

Read unknown blocks one at a time, and check the camera nodes after each.

Device data contains the serial number and calibration, so keep it out of this repo.

## Depth mode switching

Date: 2026-10-31. Source: `ferret-mode`, Y14 at 1280x800, tripod-mounted scanner facing
a box, shelf and monitor. Distances in this scene were not measured.

Set structure (opcode 28) takes property ID 1043, command version 0 and a 52-byte record
copied from raw block 4034. The header size field is 29 words, and the request is padded
to 512 bytes on control 1. The SDK requires all streams to be stopped first.

| Mode | Frame with depth | Repeat |
|---|---|---|
| `Close_range_mode Default` | 0% | 0% |
| `Long_range_mode Default` | 61.8% | 60.2% |

- The scanner accepted each switch at once, without leaving the USB bus or restarting.
- A read after each switch reported the new mode.
- Long-range values were about 2300 on the monitor and about 4600 on the box. The value
  scale differs from close range, so close-range calibration does not carry over.
- Not tested yet: whether the mode survives a power cycle, long-range distance limits,
  depth accuracy, and how the official app behaves afterwards.

## Converting depth values to distance

Date: 2026-10-31. Source: tape measurements compared with values stored in the scanner.

Raw block 4026 is 28 bytes. These values were read in both modes:

| Field | Close range | Long range |
|---|---|---|
| Byte offset 4, float | 149.907 | 149.907 |
| Byte offset 8, float | 657.9214 | 1434.2688 |
| Byte offset 12, float | 956.7727 | 956.7727 |
| Byte offset 16, float | 0.5 | 0.5 |
| Byte offset 20, float | 218 | 100 |

Y14 values fit this formula, where value 0 means no depth:

```
distance_mm = unit * baseline * focal / (value / 64 + offset)
```

Here `baseline` is 149.907, `focal` is 956.7727, `unit` is 0.5 and `offset` is the mode's
value at byte 20. The field names are inferred from how they behave. The 64 fits 6
fractional bits in a 14-bit value with 8 integer bits, the SDK's stated default.

| Mode | Value | Tape | Formula |
|---|---|---|---|
| Close | 8813 | About 20 cm | 20.2 cm |
| Close | 1034 | About 30 cm | 30.6 cm |
| Long | 4662 | About 41 cm | 41.5 cm |
| Long | 2189-2350 | About 51.7 cm | 52.4-53.4 cm |

- The formula reads about 1-3% long against the tape. The tape started at the middle of
  the scanner, which may not be the optical origin.
- The value at byte 8 equals `baseline * focal / offset`, the far limit in 0.5 mm units.
- The 14-bit range gives about 151-329 mm in close range and 201-717 mm in long range.
  Together they match the 150-700 mm quoted in listings.
- `focal` only applies to 1280x800. Other sizes are untested.
- This gives distance along the camera axis at best. Lens centre and distortion are not
  known yet, so 3D accuracy is unmeasured.
