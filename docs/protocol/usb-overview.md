# USB overview: CR-Scan Ferret Pro

Source: USB descriptors and Linux kernel log, read without streaming.
Date: 2026-10-31. Host: Ubuntu, kernel 7.0.

## Identity

- USB ID: `2bc5:069c` (Orbbec vendor ID)
- Product string: `CR-Scan Ferret`, including on this Ferret Pro
- Power: bus powered, requests 500 mA
- Linux binds `uvcvideo` to the cameras and `usbhid` to the IMU, with no vendor
  driver needed.

## Interfaces

| Interface | Function | Linux driver |
|---|---|---|
| 0, 1 | Depth camera control and stream | uvcvideo |
| 2, 3 | IR camera control and stream | uvcvideo |
| 4, 5 | RGB camera control and stream | uvcvideo |
| 6 | IMU (`CR-Scan FerretIMU`) | usbhid |

All video streams use bulk transfers (512-byte packets at USB 2 speed).

## Video modes seen at 480 Mbit/s

Every mode offers 15, 13, 12, 10 and 5 fps. 15 fps is the maximum in this
connection state.

| Stream | Formats | Sizes |
|---|---|---|
| Depth | Y14, Y10, Y8, MJPEG | 320x200, 640x400, 1280x800; 800x600 except Y10 and MJPEG |
| IR | Y10, Y8, MJPEG | 320x200, 640x400, 1280x800; 800x600 for Y8 only |
| RGB | MJPEG | 320x240, 640x360, 640x480, 1280x720, 1280x800, 1280x960, 1920x1080 |

Y10 and Y14 frame sizes match packed pixels: 1280x800 Y14 is 1,792,000 bytes.
At 15 fps that stream alone is 215 Mbit/s.

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

- Does the scanner reach 5 Gbit/s with another cable? Listings quote 30 fps, but
  it reported 15 fps at most at 480 Mbit/s. The USB 2 descriptors' device
  qualifier has `bMaxPacketSize0 = 9`, which may hint at a USB 3 mode. Unproven.
- Which stream carries the depth used for scanning, and is it computed on the
  scanner or from raw frames on the PC?
- Where is the factory calibration stored, and how is it read?
- Does streaming need vendor commands first?
- How does the WiFi bridge carry the data?

No stream has been started yet, so frames, calibration and IMU data are untested.
