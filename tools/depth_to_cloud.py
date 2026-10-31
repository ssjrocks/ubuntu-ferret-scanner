#!/usr/bin/env python3
"""Turns a raw Y14 depth capture into a point cloud and optional preview images.

The formula is in docs/protocol/commands.md. Lens values are the scanner's factory
calibration from block 4027, which lists no depth distortion.
"""
import argparse
import warnings

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 1280, 800
FRAME_BYTES = WIDTH * HEIGHT * 14 // 8
UNIT = 0.5
BASELINE = 149.907
FOCAL = 956.7727
CENTRE_X = 645.56445
CENTRE_Y = 401.40128
MODE_OFFSETS = {"close": 218.0, "long": 100.0}
MAX_FRAMES = 24


def decode_y14(data):
    bits = np.unpackbits(data)[: 14 * WIDTH * HEIGHT].reshape(-1, 14)
    return (bits @ (1 << np.arange(13, -1, -1))).reshape(HEIGHT, WIDTH)


def load_depth_mm(path, offset):
    data = np.fromfile(path, dtype=np.uint8)
    count = len(data) // FRAME_BYTES
    if count == 0:
        raise SystemExit(f"no complete frames in {path}")

    first = max(count - MAX_FRAMES, 0)
    frames = np.stack(
        [decode_y14(data[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]) for i in range(first, count)]
    ).astype(np.float32)
    frames[frames == 0] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # pixels with no depth in any frame
        value = np.nanmedian(frames, axis=0)
    return UNIT * BASELINE * FOCAL / (value / 64 + offset)


def to_points(depth):
    rows, cols = np.nonzero(~np.isnan(depth))
    z = depth[rows, cols]
    x = (cols - CENTRE_X) * z / FOCAL
    y = (CENTRE_Y - rows) * z / FOCAL
    return np.column_stack((x, y, z)).astype(np.float32)


def write_ply(path, points):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n"
    )
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(points.astype("<f4").tobytes())


def render(points, yaw, pitch, path, size=1100):
    """Draws the cloud from a turned viewpoint, nearest point winning at each pixel."""
    centre = np.median(points, axis=0)
    p = points - centre
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    x = p[:, 0] * cy + p[:, 2] * sy
    z = -p[:, 0] * sy + p[:, 2] * cy
    y = p[:, 1] * cp - z * sp
    z = p[:, 1] * sp + z * cp

    scale = size * 0.9 / max(np.ptp(x), np.ptp(y))
    u = np.clip((x * scale + size / 2).astype(int), 0, size - 2)
    v = np.clip((size / 2 - y * scale).astype(int), 0, size - 2)

    shade = np.clip(1 - (points[:, 2] - points[:, 2].min()) / np.ptp(points[:, 2]), 0, 1)
    colour = np.column_stack((60 + 195 * shade, 170 - 60 * np.abs(shade - 0.5), 255 - 195 * shade))

    image = np.zeros((size, size, 3), dtype=np.uint8)
    for i in np.argsort(-z):
        image[v[i] : v[i] + 2, u[i] : u[i] + 2] = colour[i]
    Image.fromarray(image).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw")
    parser.add_argument("--mode", choices=MODE_OFFSETS, default="long")
    parser.add_argument("--out", required=True, help="output path without an extension")
    args = parser.parse_args()

    depth = load_depth_mm(args.raw, MODE_OFFSETS[args.mode])
    points = to_points(depth)
    write_ply(args.out + ".ply", points)
    print(f"{len(points)} points, z {points[:, 2].min():.0f}-{points[:, 2].max():.0f} mm")

    for name, yaw in (("front", 0.0), ("left", -0.75), ("right", 0.75)):
        render(points, yaw, 0.2, f"{args.out}-{name}.png")


if __name__ == "__main__":
    main()
