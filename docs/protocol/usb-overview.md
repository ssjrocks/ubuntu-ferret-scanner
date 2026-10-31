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
  `20343159-0000-0010-8000-00aa00389b71` (Y14), so that format may not be usable
  through V4L2.
- The kernel logs a failed `GET_INFO` on unit 3 of the depth camera, control 18.

## Open questions

- Do Y10 and Y14 carry full-range depth, and in what units?
- Where is the factory calibration stored, and how is it read?
- Does valid depth need vendor commands first?
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
- Only the part of the scene in one corner had data, while IR showed the projected
  dots across the whole scene. Y8 may be cutting off most of the depth range. This
  is unproven.
- Units and the meaning of the values are unknown.
- Depth MJPEG at 640x400 did not decode as JPEG.
