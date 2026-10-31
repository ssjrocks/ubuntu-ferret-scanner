# Scan tests

## First handheld desk pass

Date: 2026-10-31. Source: 20 seconds of long-range Y14 at 1280x800, aligned offline by
`tools/align_pass.py`. Colour was recorded but is not used yet.

| Measure | Result |
|---|---|
| Frames captured | 598, plus 3 dropped at the start |
| Frames aligned | 598, none lost |
| Matched depth points | 85% median, 75.5% at the 5th percentile |
| Frame-to-keyframe error | 0.65 mm median RMS, 1.17 mm worst frame |
| Scanner path | About 1.6 m, up to 89 degrees of turn |
| Scanner speed | About 83 mm/s median, 154 mm/s peak |
| Combined cloud | About 1.66 million points at 1.5 mm spacing |

- A relief view of the cloud shows the keyboard rows, its outline and desk objects as
  single, readable shapes.
- There is no measured accuracy yet. Tracking errors only show how well each frame fits
  its keyframe.
- The pass did not return to its start, so drift was neither corrected nor measured.
- The lens centre is assumed, distortion is ignored, and the motion sensor is unused.
- `align_pass.py` ran offline under an 8 GB limit and took longer than the 20-second
  capture. Its real memory use was not measured.
