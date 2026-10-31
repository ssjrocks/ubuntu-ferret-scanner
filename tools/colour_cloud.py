#!/usr/bin/env python3
"""Colours a long-range depth frame from a matching 1920x1080 colour image.

Focal length, distortion and the camera transform are factory values from block 4027. They
are listed for one colour mode, so the 1920x1080 image centre is a measured estimate. See
docs/protocol/camera-layout.md.
"""
import argparse

import numpy as np
from PIL import Image

from depth_to_cloud import CENTRE_X, CENTRE_Y, FOCAL, HEIGHT, MODE_OFFSETS, WIDTH, load_depth_mm

COLOUR_SCALE = 1.5  # from 1280-wide factory values to 1920x1080
COLOUR_FOCAL_X = 839.90887 * COLOUR_SCALE
COLOUR_FOCAL_Y = 839.55103 * COLOUR_SCALE
COLOUR_CX = 655.0 * COLOUR_SCALE
COLOUR_CY = 366.0 * COLOUR_SCALE
K1, K2, K3 = 0.11165, -0.33142, 0.25619
P1, P2 = 0.00076, 0.00035
ROTATION = np.array(
    [[1.0, 0.00237, 0.00183], [-0.00237, 1.0, 0.00058], [-0.00183, -0.00059, 1.0]]
)
TRANSLATION_MM = np.array([19.47028, -0.18568, -0.66741])


def project_to_colour(points):
    """Maps depth-camera points (x right, y down, z forward) to 1920x1080 pixels."""
    q = (points - TRANSLATION_MM) @ ROTATION
    a, b = q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]
    r2 = a * a + b * b
    radial = 1 + K1 * r2 + K2 * r2**2 + K3 * r2**3
    u = a * radial + 2 * P1 * a * b + P2 * (r2 + 2 * a * a)
    v = b * radial + P1 * (r2 + 2 * b * b) + 2 * P2 * a * b
    return COLOUR_CX + COLOUR_FOCAL_X * u, COLOUR_CY + COLOUR_FOCAL_Y * v


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
    x = (cols - CENTRE_X) * z / FOCAL
    y = (rows - CENTRE_Y) * z / FOCAL

    u, v = project_to_colour(np.column_stack((x, y, z)))
    u, v = np.round(u).astype(int), np.round(v).astype(int)
    seen = (u >= 0) & (u < colour.shape[1]) & (v >= 0) & (v < colour.shape[0])
    print(f"{seen.sum()} of {len(z)} points have colour")

    picture = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    picture[rows[seen], cols[seen]] = colour[v[seen], u[seen]]
    Image.fromarray(picture).save(args.out + "-painted.png")

    points = np.column_stack((x, -y, z))[seen].astype(np.float32)
    write_colour_ply(args.out + ".ply", points, colour[v[seen], u[seen]])


if __name__ == "__main__":
    main()
