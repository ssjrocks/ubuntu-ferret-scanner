#!/usr/bin/env python3
"""Colours a long-range depth frame from a matching 1920x1080 colour image.

The camera offset is the sweep estimate in docs/protocol/camera-layout.md. Rotation between
the cameras and lens distortion are ignored, so colour may slip near the frame edges.
"""
import argparse

import numpy as np
from PIL import Image

from depth_to_cloud import FOCAL, HEIGHT, MODE_OFFSETS, WIDTH, load_depth_mm

COLOUR_SCALE = 1.5  # from the 1280x720 sweep to 1920x1080
COLOUR_FOCAL = 0.861 * FOCAL * COLOUR_SCALE
COLOUR_CX = 653.0 * COLOUR_SCALE
COLOUR_CY = 391.0 * COLOUR_SCALE
OFFSET_X_MM = -20.0
OFFSET_Y_MM = -13.0


def write_colour_ply(path, points, colours):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    record = np.dtype([("p", "<f4", 3), ("c", "u1", 3)])
    data = np.empty(len(points), dtype=record)
    data["p"], data["c"] = points, colours
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(data.tobytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw")
    parser.add_argument("colour")
    parser.add_argument("--mode", choices=MODE_OFFSETS, default="long")
    parser.add_argument("--out", required=True, help="output path without an extension")
    args = parser.parse_args()

    depth = load_depth_mm(args.raw, MODE_OFFSETS[args.mode])
    colour = np.asarray(Image.open(args.colour).convert("RGB"))
    rows, cols = np.nonzero(~np.isnan(depth))
    z = depth[rows, cols]
    x = (cols - WIDTH / 2) * z / FOCAL
    y = (rows - HEIGHT / 2) * z / FOCAL

    u = np.round(COLOUR_CX + COLOUR_FOCAL * (x + OFFSET_X_MM) / z).astype(int)
    v = np.round(COLOUR_CY + COLOUR_FOCAL * (y + OFFSET_Y_MM) / z).astype(int)
    seen = (u >= 0) & (u < colour.shape[1]) & (v >= 0) & (v < colour.shape[0])
    print(f"{seen.sum()} of {len(z)} points have colour")

    picture = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    picture[rows[seen], cols[seen]] = colour[v[seen], u[seen]]
    Image.fromarray(picture).save(args.out + "-painted.png")

    points = np.column_stack((x, -y, z))[seen].astype(np.float32)
    write_colour_ply(args.out + ".ply", points, colour[v[seen], u[seen]])


if __name__ == "__main__":
    main()
