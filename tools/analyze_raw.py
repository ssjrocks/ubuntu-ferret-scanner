#!/usr/bin/env python3
"""Tries packed-pixel layouts on a raw capture and scores each one.

The right layout gives smooth surfaces, so its neighbouring pixels differ far less
than those from a wrong layout.
"""
import argparse
import csv

import numpy as np
from PIL import Image


def bitstream(data, bits, count, little):
    order = "little" if little else "big"
    unpacked = np.unpackbits(data, bitorder=order)[: bits * count]
    weights = 1 << np.arange(bits) if little else 1 << np.arange(bits - 1, -1, -1)
    return unpacked.reshape(count, bits) @ weights


def swapped(data, word):
    usable = len(data) - len(data) % word
    return data[:usable].reshape(-1, word)[:, ::-1].ravel()


def mipi(data, bits, count):
    """MIPI RAW10 and RAW14: high bytes first, then the shared low bits."""
    pixels, tail = {10: (4, 1), 14: (4, 3)}[bits]
    group = pixels + tail
    groups = len(data) // group
    block = data[: groups * group].reshape(groups, group).astype(np.uint32)
    low = np.unpackbits(block[:, pixels:].astype(np.uint8), axis=1, bitorder="little")
    low_bits = bits - 8
    weights = 1 << np.arange(low_bits)
    values = np.empty((groups, pixels), dtype=np.uint32)
    for i in range(pixels):
        values[:, i] = (block[:, i] << low_bits) | (low[:, i * low_bits : (i + 1) * low_bits] @ weights)
    return values.ravel()[:count]


def layouts(data, bits, count):
    yield "msb bitstream", bitstream(data, bits, count, False)
    yield "lsb bitstream", bitstream(data, bits, count, True)
    for word in (2, 4, 8):
        yield f"msb bitstream, {word}-byte swap", bitstream(swapped(data, word), bits, count, False)
        yield f"lsb bitstream, {word}-byte swap", bitstream(swapped(data, word), bits, count, True)
    yield "mipi", mipi(data, bits, count)


def score(image):
    left, right = image[:, :-1].astype(np.int64), image[:, 1:].astype(np.int64)
    both = (left > 0) & (right > 0)
    if both.sum() == 0:
        return float("inf"), 0.0
    return float(np.median(np.abs(left - right)[both])), float(np.mean(image > 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix")
    parser.add_argument("--bits", type=int, required=True)
    parser.add_argument("--size", default="1280x800")
    parser.add_argument("--frame", type=int, default=45)
    parser.add_argument("--png")
    args = parser.parse_args()

    width, height = map(int, args.size.split("x"))
    with open(args.prefix + ".csv") as f:
        rows = list(csv.DictReader(f))
    expected = width * height * args.bits // 8
    sizes = [int(r["bytes"]) for r in rows]
    print(f"frames {len(rows)}, wrong size at {[i for i, s in enumerate(sizes) if s != expected][:20]}")
    print(f"flagged at {[i for i, r in enumerate(rows) if r['error'] == '1'][:20]}")

    pts = np.array([int(r["pts"]) for r in rows], dtype=np.int64)
    print(f"pts step median {np.median(np.diff(pts))}, min {np.diff(pts).min()}, max {np.diff(pts).max()}")

    offset = sum(sizes[: args.frame])
    data = np.fromfile(args.prefix + ".raw", dtype=np.uint8, count=expected, offset=offset)
    count = width * height
    results = []
    for name, values in layouts(data, args.bits, count):
        image = np.asarray(values).reshape(height, width)
        results.append((*score(image), name, image))
    results.sort(key=lambda r: r[0])
    for median_step, filled, name, image in results:
        print(f"{name:28} step {median_step:8.1f}  filled {filled:6.1%}  max {image.max()}")

    best = results[0][3]
    valid = best[best > 0]
    if valid.size:
        print("best layout percentiles 1/50/99:", np.percentile(valid, [1, 50, 99]))
    if args.png and valid.size:
        low, high = np.percentile(valid, [1, 99])
        scaled = np.clip((best.astype(float) - low) / max(high - low, 1), 0, 1)
        Image.fromarray((np.where(best > 0, 40 + scaled * 215, 0)).astype(np.uint8)).save(args.png)


if __name__ == "__main__":
    main()
