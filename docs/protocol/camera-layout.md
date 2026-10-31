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

## Stored values

- Raw block 4026 holds 149.907 with a unit of 0.5, which is 74.95 mm. It matches the two
  large lenses, and it agrees with the 7.5 in struct 1002.
- Struct 1001 is all NaN.
- Struct 1005 (502 bytes) has device strings and scattered numbers with no recognisable
  lens centre or distortion values, so it is not the lens data.
- Lens values may be in raw block 4027, which hung the scanner. It stays off limits unless
  it is deliberately retried.

## Colour offset

The lighter box's centre was measured in both cameras at three distances (about 20, 30 and
41 cm), then fitted to the expected shift with distance.

| Value | Estimate |
|---|---|
| Sideways offset | About 20 mm, within 0.3 px with assumed colour focal length |
| Vertical offset | About 13 mm, weaker: 4 px residual and no matching photo offset |
| Colour focal length | About 1236 px at 1920x1080 |

- The sideways result matches the 20 mm between openings 1 and 2 in the photo, which
  suggests depth is measured from opening 1.
- The vertical term may be standing in for a small tilt between the cameras.
- Three positions give only one spare check per axis.
- Painting a depth frame with this mapping put the box, tape marks and on-screen text in the
  right places by eye. This was not measured, and the frame edges may slip.

## Width finding

The box's colour-to-depth width ratio stayed at 0.861-0.862 from 189 to 382 depth pixels wide.
Fixed-size edge spreading would change that ratio, so edge blur is unlikely to explain the
box reading 4-5% wide. The horizontal scale of depth needs another look.
