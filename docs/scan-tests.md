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

### Scale check

A 2 mm view with a ruler grid shows the keyboard about 440 mm across, or about 443 mm along
its slightly rotated length. The edges were read by eye to about +/- 10 mm. A tape across
the real keyboard gave 440-450 mm. An earlier 425 mm figure came from a coarser picture
and was wrong.

This suggests the combined cloud's scale is right to within a few percent over about
44 cm. It is one object on one pass, so it is not an accuracy figure.

## Loop closure

Date: 2026-10-31. `tools/align_pass.py` now records keyframes, checks each new keyframe
against earlier nearby ones, and corrects the path with `tools/pose_graph.py`. A loop match
needs at least 45% matched points, at most 1.5 mm RMS, and enough shape to fix position in
every direction, which guards against flat scenes sliding.

- Synthetic test: a loop edge removes at least 95% of injected drift on a 40-pose circle.
  This tests the optimiser only, not loop detection on real data.
- The desk pass found no loops, as expected from a pass that did not revisit its start,
  and it produced the same point count as before: 1,663,314.
- Real loop detection and drift are untested until a pass returns to its start.

## First looping pass

Date: 2026-10-31. Source: 30 seconds of long-range Y14, handheld, circling the desk about
twice with overlap. It did not end at its starting view.

| Measure | Result |
|---|---|
| Frames aligned | 897 of 897 |
| Frame-to-keyframe error | 0.54 mm median RMS, 1.21 mm worst frame |
| Scanner path | About 3.1 m |
| Scanner speed | About 100 mm/s median, 286 mm/s peak |

| Loop search | Matches | Largest gap | Median surface spread |
|---|---|---|---|
| None used for comparison | Not applicable | Not applicable | Not measured |
| Default, 150 mm and 0.5 rad | 10 | 1.3 mm, 0.22 deg | 2.9 mm |
| Wide, 300 mm and 1.2 rad | 72 | 3.7 mm, 0.97 deg | 4.17 mm |

Surface spread is the median thickness in 8 mm columns of the cloud, across all surfaces.

- The wide search matched the end of the path to its first keyframe. The gap there was
  about 3.7 mm and 1 degree after about 2.6 m of travel. This is the first real drift
  measurement, from one pass.
- Correcting with the wide search made the surfaces thicker. Loop correction is not an
  improvement yet, so the default remains the narrow search.
- The cause is unproven. Unmodelled lens distortion is the main suspect, because the same
  surface seen from different views would then disagree slightly.

### Retest with factory calibration

The tools now use the stored lens centre. The stored depth distortion is zero, which rules out
the distortion theory above.

| Loop search | Matches | Median surface spread |
|---|---|---|
| Off | 0 | 2.90 mm |
| Default | 10 | 2.89 mm |
| Wide | 73 | 4.06 mm |

Default correction has almost no effect, and wide correction still makes surfaces thicker. The
cause is unknown. Candidates are poor wide matches, the fixed loop weight, or this
whole-cloud measure mixing in real shape. The default stays narrow.

## First coloured scan

Date: 2026-10-31. Source: `tools/colour_pass.py` on the looping pass, using poses from the
default loop search and every 4th depth frame.

- Result: about 1.57 million coloured points. Viewed from above, the keyboard, game controller,
  mouse, radio and tape are recognisable, with readable key rows by eye.
- Depth and colour were recorded by separate programs. The first sync attempt matched scanner
  speed to picture change, gave a weak result at the edge of its search, and smeared the
  colour. Its frame numbers also slipped where colour frames failed to decode.
- The working method keeps each colour frame's real timestamp, then tests which time shift
  lands depth edges on colour edges in the fastest frames. It found 178 ms, with a score of
  23.26 against a typical 19.57 and a spread of 1.17.
- Colour is a plain average, so uneven lighting and screen glow show as patches.
- Some areas still show smearing or doubling, especially around the edges of the scan.
- The colour centre is the rough value in `protocol/camera-layout.md`, so colour accuracy is
  unmeasured.
- The tool stops if the best time shift lands at the edge of its search.

## Multi-lap build fix

Date: 2026-10-31. Source: a 74-second, 2,215-frame handheld desk scan from the app, about
7.2 m of movement over several laps. Its first model had doubled, smeared surfaces.

Two changes fixed most of this:

- The pose graph treated rotation error as far cheaper than position error. Errors were hidden
  in rotation, which bends surfaces. Rotation is now weighted by the square of a 400 mm working
  distance. This fits the earlier result where more loop matches made surfaces thicker.
- Alignment now refines over four rounds. Each round matches overlapping keyframes using the
  corrected path, with a tighter search, and drops matches the solver can't agree with.

| Measure | Before | After |
|---|---|---|
| Median surface spread, 6 mm columns | 17.6 mm | 6.0 mm |
| Cloud points | 9.15 million | 3.09 million |
| Largest gap | 30 mm, not resolved | 31.9 mm before correction, 4.9 mm after |
| Loop matches used | 223 | 470 |
| Build time | 116 s | 191 s |

- Viewed by eye, the keyboard now shows single keys, and the desk and nearby objects are single
  surfaces. Accuracy against a reference is not measured.
- The 30-second scan still builds, with a 2.6 mm gap before correction and 0.5 mm after.
- Meshes now drop loose pieces under 300 faces, and voxels need at least 6 samples.
- Tracking can rejoin earlier keyframes after being lost, but this has only worked once.
- Moving subjects are not supported. The method assumes the whole scene is rigid, so a person
  shifting or breathing breaks tracking and fusion.
- Flat desk areas still show faint ripples. Cause unknown.
