# ferret-capture

Saves raw frames from the scanner's UVC streams, including Y14, which the Linux
camera driver cannot read. It sends only standard UVC requests.

Status: `--list` is tested on a Ferret Pro. Streaming has not been tested yet.

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
