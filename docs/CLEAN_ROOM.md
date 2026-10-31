# Clean-room rules

The aim is software that could be shared publicly later without containing any
of Creality's work. These are engineering rules, not legal advice.

## Keep out of this repo and its history

- Creality Scan installers, DLLs, EXEs, firmware or other vendor files.
- Decompiled or disassembled code, in any language, even short snippets.
- Shaders, models, images, sounds or text from the official app.
- Keys, tokens or secrets taken from the app, device or firmware.
- Raw captures that contain account details or device secrets.

Git history is permanent once public, so a mistake must be removed from history,
not just deleted in a later commit.

## Where reverse-engineering work goes

Store vendor files and tool output in `~/dev/ferret-re-private/`, outside this
repo. It is never pushed anywhere.

## How findings enter the project

Prefer sources in this order:

1. Public documentation, standards and open-source SDKs.
2. The device's own behaviour: USB descriptors, and USB or network traffic.
3. The official app's interfaces, using inspection of only what interoperability
   needs: imports, exports, strings and the specific protocol code.

Record findings in `docs/protocol/` as facts in our own words: message layouts,
field meanings, ordering and timing. Then write code from those notes, without
the decompiled output open.

Each finding records how it was learned, such as `USB capture` or
`SDK documentation`.

## Do not build

- Bypasses for licence, payment, account or device-authentication checks.
- Decryption of vendor-protected content.
- Unofficial use of Creality's servers, accounts or cloud APIs.

Stop and discuss it first if the scanner turns out to need any of these for
normal use. Firmware writes also need separate discussion, because they can
damage the device.

## Algorithms

Build reconstruction from published methods and compatibly licensed libraries.
Record each third-party dependency and its licence before adding it. Do not port
the official app's implementation from decompiled code.
