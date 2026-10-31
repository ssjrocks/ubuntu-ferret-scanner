# USB overview: CR-Scan Ferret Pro

Source: USB descriptors and Linux kernel log, read without streaming.
Date: 2026-10-31. Host: Ubuntu, kernel 7.0.

## Identity

- USB ID: `2bc5:069c` (Orbbec vendor ID)
- Product string: `CR-Scan Ferret`, including on this Ferret Pro
- Power: bus powered, requests 896 mA at 5 Gbit/s and 500 mA at 480 Mbit/s
- Linux binds `uvcvideo` to the cameras and `usbhid` to the IMU, with no vendor
  driver needed.

## Interfaces

| Interface | Function | Linux driver |
|---|---|---|
| 0, 1 | Depth camera control and stream | uvcvideo |
| 2, 3 | IR camera control and stream | uvcvideo |
| 4, 5 | RGB camera control and stream | uvcvideo |
| 6 | IMU (`CR-Scan FerretIMU`) | usbhid |

All video streams use bulk transfers (1024-byte packets with bursts of up to 16 at
5 Gbit/s, 512-byte packets at 480 Mbit/s).

## Video modes

The app should require 5 Gbit/s and warn at 480 Mbit/s.

| Stream | Formats | Sizes |
|---|---|---|
| Depth | Y14, Y10, Y8, MJPEG | 320x200, 640x400, 1280x800; 800x600 except Y10 and MJPEG |
| IR | Y10, Y8, MJPEG | 320x200, 640x400, 1280x800; 800x600 for Y8 only |
| RGB | MJPEG, YUY2 (5 Gbit/s only) | 320x240, 640x360, 640x480, 1280x720, 1280x800, 1280x960, 1920x1080 |

| Link | Frame rates | Modes |
|---|---|---|
| 5 Gbit/s | 30, 20, 15, 10, 5 | 38 |
| 480 Mbit/s | 15, 13, 12, 10, 5 | 31 |

Y10 and Y14 frame sizes match packed pixels: 1280x800 Y14 is 1,792,000 bytes.
Advertised peak rates at 30 fps, from the descriptors:

- Depth 1280x800 Y14: 430 Mbit/s
- IR 1280x800 Y10: 307 Mbit/s
- RGB 1920x1080 YUY2: 995 Mbit/s

These are advertised modes. None has been streamed yet.

## Vendor control units

- Depth: `a55751a1-f3c5-4a5e-8d5a-6854b8fa2716`, 17 controls
- IR: `4242524f-0000-0000-0000-000000000001`, 255 controls
- RGB: `4242524f-0000-0000-0000-000000000002`, 255 controls

These may be how the app configures the scanner. Their use is unknown.

## IMU

The 25-byte HID report descriptor declares usage page `0x8C` and one input report
of 62 fields, 32 bits each. The data layout has not been read.

## Known issues

- The kernel logs `Unknown video format` for GUID
  `20343159-0000-0010-8000-00aa00389b71` (Y14), and V4L2 does not offer that format.
  Direct UVC access through libusb works.
- The kernel logs a failed `GET_INFO` on unit 3 of the depth camera, control 18.

## Open questions

- What are the units of Y14 depth, and how do values map to distance?
- What sets the depth working range, and can it be widened?
- Where is the factory calibration stored, and how is it read?
- What does the official app change to reach past 40 cm? See `commands.md`.
- How does the WiFi bridge carry the data?

## First stream test

Date: 2026-10-31. Source: 3-second V4L2 captures at 5 Gbit/s, one stream at a time,
with the scanner on a desk facing a ceiling. No vendor commands were sent.

| Device | Stream | Mode | Measured | Result |
|---|---|---|---|---|
| `/dev/video4` | RGB | MJPEG 1920x1080 | 30.0 fps, 91 frames | Clear image |
| `/dev/video4` | RGB | YUYV 1920x1080 | 30.0 fps, 90 frames | Clear image, see below |
| `/dev/video2` | IR | Y8 1280x800 | 29.7 fps, 90 frames | Projected dot pattern visible |
| `/dev/video0` | Depth | Y8 1280x800 | 30.0 fps, 91 frames | Every sampled frame all zero |

`/dev/video1`, `3` and `5` are not capture nodes. They are probably UVC metadata
nodes, which is unconfirmed.

- The IR dot pattern shows the projector ran with standard UVC streaming alone.
- ffmpeg flagged YUYV buffers as corrupted (about 12-48 KB reported) throughout
  the clip, yet all 90 frames were unique and every sampled row changed between
  neighbouring frames. The cause is unknown, so YUYV is not yet trustworthy.
- The all-zero depth was with a distant ceiling. Depth appeared at close range,
  see below.
- Y10 and Y14 could not be captured with ffmpeg: Y10 is listed as unsupported, and
  the kernel does not list Y14 at all.
- Streams have not been run together. Calibration, IMU and WiFi are untested.

## Close-range depth test

Date: 2026-10-31. Source: 2-second V4L2 captures, scanner aimed at a desk with a
keyboard and objects from about 40 cm. No vendor commands were sent.

- `/dev/video0` in Y8 gave real structure: keyboard rows and key gaps are visible
  in the 640x400 frames. The USB stream therefore carries depth-like data computed
  on the scanner, not only raw images.
- Sampled frames were 8-16% non-zero at 1280x800 and up to about 20% at 640x400,
  with a maximum value of 61.
- Only part of the scene had data, while IR showed the projected dots across the
  whole scene. Y14 showed the same limit, so Y8 is not the cause.
- Depth MJPEG at 640x400 did not decode as JPEG.

## Raw stream test

Date: 2026-10-31. Source: `tools/ferret-capture`, with layouts scored by
`tools/analyze_raw.py` on frames from the same desk scene.

- Depth Y14 at 1280x800 ran at 30.0 fps. The first run on a rested stream had no
  dropped frames, and individual keyboard keys are visible.
- Pixels are a big-endian bitstream, most significant bit first: 7 bytes hold 4
  Y14 pixels. The step between neighbouring pixels was 4 for this layout and 513 or
  more for all others tried. IR Y10 uses the same layout (step 8, next best 32).
- Depth value 0 appears to mean no data. Y14 values in the scene had a median of
  1290 and a maximum of 2752.
- Values grew towards the nearer end of the keyboard, so they do not behave like a
  plain distance. They may be disparity-like. Units are unknown.
- About 21% of the frame had depth, with values falling smoothly towards 0 at the
  far edge of the keyboard. This suggests a limited working window, but what sets
  it is unknown.
- Payload size negotiated at 12288 bytes. PTS steps of about 33318 suggest a
  microsecond clock.
- A stream started soon after another on the same camera begins with leftover data,
  which shifts reads off payload boundaries. `ferret-capture` now resyncs on the next
  short transfer and drops bad frames, losing 1-3 frames at the start. The cause of
  the leftover data is not proven.
- The earlier YUYV warnings had the same sizes (multiples of about 12276 bytes,
  12288 minus a 12-byte header), so they probably have the same cause. Unconfirmed.
- Depth Y14, IR Y8 and RGB MJPEG ran together at 29.7, 29.4 and 28.8 fps with no
  failed depth transfers. The scanner was facing a distant ceiling, so depth quality
  under this load is untested.
- Depth Y10 was not repeated after the resync fix, so its depth content is untested.

## Desk scene test

Date: 2026-10-31. Source: `ferret-capture`, scanner propped on a desk and facing
objects at mixed distances. Actual distances were not measured.

- Depth Y14 ran at 29.3 fps with 2 frames dropped at the start. Object edges are
  sharp. A box, a bottle and a drawer unit are clearly separated.
- About 11% of the frame had depth, and the first sampled frame had 5%. Values
  ranged from about 6 to 9000 (1st to 99th percentile), with nearer objects higher.
- The IR image is mirrored left to right compared with colour and depth.
- IR showed dots on many surfaces with no depth, including part of a shelf and a blue
  plastic tub that looked about as near as the box. A distance limit may not be
  the whole explanation. Surface type or a setting could also play a part.

## First distance estimate

Date: 2026-10-31. Source: tape measurements on the desk scene above, compared with
Y14 values. This is a rough estimate from two points, not calibration.

| Point | Measured distance | Y14 value |
|---|---|---|
| Lighter box, far end | 32.5 cm | About 2150 |
| Drawer unit, handle area | 31.5-37.5 cm, depending on the scanner edge used | About 600, +/- 250 |

- Y14 values change at a nearly constant rate across the flat box, about 7.6 per
  pixel. Inverse depth behaves this way on a plane and plain distance does not, so
  the values are probably disparity-like. Not proven.
- Fitting `value = K / distance - c` to these points puts value 0 at about 35-40 cm.
  That fits depth stopping just past the drawers, and is well short of the 70 cm
  quoted in listings.
- The estimate is weak. The scanner's reference point is unknown, the points were
  matched by eye between cameras, and two points fit any curve of this form.

Next test: keep the scanner still and move a flat card to several measured distances.

## Distance sweep

Date: 2026-10-31. Source: Y14 at 1280x800 with no vendor commands, using a flat box
square-on to the scanner. Distances are tape measurements from the box to the middle
of the scanner.

| Distance | Box value | Frame with depth |
|---|---|---|
| About 20 cm | About 8813 (5th-95th percentile 8750-8878) | 51.5% |
| About 30 cm | About 1034 (994-1085) | 31.7% |
| About 40 cm | No depth anywhere in the frame | 0% |

- Depth stops completely between about 30 and 40 cm in this mode. Fitting
  `value = K / distance - c` to the first two rows puts value 0 at about 32 cm. That
  replaces the 35-40 cm estimate.
- The official app reaches past 40 cm according to the owner, so something beyond the
  default stream state sets the range. What that is remains unknown.
- The 50 and 60 cm positions were skipped because 40 cm was already empty.
