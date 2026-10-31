# Camera layout

Date: 2026-10-31. Source: phone photos with a tape, depth and colour captures, and values
stored in the scanner. These are rough estimates, not calibration.

## Front of the scanner

Looking at the front, left to right, one head-on photo shows four openings in a line. The
roles come from their appearance and the checks below.

| Opening | Likely role | Position from first large lens |
|---|---|---|
| 1 | Large lens, IR camera | 0 mm |
| 2 | Small lens, colour camera | About 20 mm |
| 3 | Square opening, laser projector | About 38 mm |
| 4 | Large lens, IR camera | About 75 mm |

The photo scale comes from setting openings 1-4 to the stored 74.95 mm. A foreground tape
can't scale it directly, because the tape sits nearer the phone than the lenses.

## Factory calibration

List block 4027 holds two 152-byte records in the SDK's `OBCameraParam_Internal_V0` layout.
They match except for image centre heights, which are 100 px lower in the second record. No
record gives its resolution. The first record's depth centre matches 1280x800.

| Value | Depth camera | Colour camera |
|---|---|---|
| Focal length | 956.77, 956.77 | 839.91, 839.55 |
| Image centre | 645.56, 401.40 | 631.75, 401.90 |
| k1, k2, k3 | 0, 0, 0 | 0.11165, -0.33142, 0.25619 |
| p1, p2 | 0, 0 | 0.00076, 0.00035 |

Rotation between the cameras is under 0.14 degrees. Translation is 19.47, -0.19 and -0.67 mm.

- Raw block 4026 holds 149.907 with a unit of 0.5, which is 74.95 mm. It matches openings 1
  and 4, and the 7.5 in struct 1002.
- Struct 1001 is all NaN. Struct 1005 holds no lens values.

## Checks against the factory values

- The box sweep gave a sideways offset of about 20 mm, against 19.47 mm stored. The 13 mm
  vertical estimate was wrong: the stored value is 0.19 mm.
- Mapped as `q = R^T (p - t)`, the stored transform gives a steady colour centre of 654-656
  px across all three distances. That is 23 px from the stored 631.75, so the 1280x720 and
  1920x1080 colour modes do not use the first record directly.
- The colour tool uses factory focal length, distortion and transform, with a measured centre
  of 655, 366 at 1280x720. Painted frames line up by eye, but this is not measured. The 1280x800
  colour mode should be tested, since it may match the record directly.

## Width finding

The box's colour-to-depth width ratio stayed at 0.861-0.862 from 189 to 382 depth pixels wide.
Fixed-size edge spreading would change that ratio, so edge blur is unlikely to explain the
box reading 4-5% wide. The stored focal length gives 80-82 mm against 78-79 mm by tape, so
the cause is still unknown.
