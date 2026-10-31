# ferret-capture

Saves raw frames from the scanner's UVC streams, including Y14, which the Linux
camera driver cannot read. It sends only standard UVC requests.

Status: tested on a Ferret Pro with depth Y14 and IR Y10 at 1280x800, 30 fps. The
first 1-3 frames can be dropped if another stream ran on that camera shortly before.

Only complete frames are saved. Y10 and Y14 pixels are packed, most significant bit
first.

## Build

```bash
sudo apt install libusb-1.0-0-dev cmake g++
cmake -S . -B build
cmake --build build
ctest --test-dir build
```

Dependency: libusb 1.0 (LGPL-2.1), linked dynamically.

## USB access

Reading descriptors needs no setup. Streaming needs write access to the device:

```bash
sudo cp 60-ferret-scanner.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Then unplug and replug the scanner.

## Use

```bash
./build/ferret-capture --list
```

```bash
./build/ferret-capture --camera depth --format Y14 --size 1280x800 --seconds 3 --out capture
```

This writes `capture.raw` (frames back to back) and `capture.csv` (one row per
frame). Keep captures outside the repo.

While it runs, the tool takes that camera from the Linux driver and gives it back on
exit. If the tool is killed, replug the scanner to restore it.
