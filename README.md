# Ubuntu Ferret Scanner

An independent, native Ubuntu app for the Creality Ferret 3D scanner, over USB
and WiFi.

**Status: nothing works yet.** No code exists, and no part of the scanner has
been tested on Linux from this project.

This project is not affiliated with or endorsed by Creality. "Creality" and
"Ferret" are used only to say which hardware this works with.

## Goals

- Run natively on Ubuntu, with no Windows, Wine or VM.
- Connect over USB, WiFi, or both.
- Match the official app's scan quality, measured on the same captures.
- Handle scans far larger than the official app can.

## Why large scans should be possible

The scanner streams data to the PC, so the link sets a data rate rather than a
maximum scan size. What limits a scan is how the software handles that data.
This project treats size as a design requirement:

- Write raw frames to disk as they arrive, so a crash loses little or nothing.
- Keep only the working area of the scan in memory, with a fixed cap.
- Build the model from chunks that can be loaded and merged independently.
- Allow an interrupted scan to be resumed or reprocessed.

Memory is only half of the problem. Tracking error builds up over long scans,
so large scans also need loop closure and global alignment, or the model will
bend and fail to meet itself. "No size limit" has to be proven on real scans.

## Clean-room rules

All code here must be written independently. See
[docs/CLEAN_ROOM.md](docs/CLEAN_ROOM.md) before contributing.

## Licence

Not chosen yet. All rights reserved until a licence is added.
