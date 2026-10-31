#!/usr/bin/env python3
"""Draws a shaded relief map of a point cloud, looking straight at its main plane."""
import argparse

import numpy as np
from PIL import Image


def read_ply(path):
    with open(path, "rb") as f:
        while f.readline().strip() != b"end_header":
            pass
        return np.frombuffer(f.read(), dtype="<f4").reshape(-1, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ply")
    parser.add_argument("png")
    parser.add_argument("--mm-per-pixel", type=float, default=1.0)
    args = parser.parse_args()

    points = read_ply(args.ply).astype(np.float64)
    centred = points - np.median(points, axis=0)
    axes = np.linalg.svd(centred[:: max(len(points) // 200000, 1)], full_matrices=False)[2]
    flat = centred @ axes.T

    low = np.percentile(flat[:, :2], 0.2, axis=0)
    high = np.percentile(flat[:, :2], 99.8, axis=0)
    size = np.ceil((high - low) / args.mm_per_pixel).astype(int) + 1
    cols, rows = ((flat[:, :2] - low) / args.mm_per_pixel).astype(int).T
    inside = (cols >= 0) & (cols < size[0]) & (rows >= 0) & (rows < size[1])

    # The sign of the third axis is arbitrary, so face the surface towards the viewer.
    height = np.full((size[1], size[0]), np.nan)
    sign = 1.0 if np.median(flat[:, 2]) >= np.mean(flat[:, 2]) else -1.0
    order = np.argsort(sign * flat[inside, 2])
    height[rows[inside][order], cols[inside][order]] = sign * flat[inside, 2][order]

    dy, dx = np.gradient(np.nan_to_num(height, nan=float(np.nanmedian(height))), args.mm_per_pixel)
    light = np.array([-0.5, -0.6, 0.62])
    shade = (light[0] * -dx + light[1] * -dy + light[2]) / np.sqrt(dx**2 + dy**2 + 1)
    image = np.clip(40 + 215 * shade, 0, 255)
    image[np.isnan(height)] = 0
    Image.fromarray(image.astype(np.uint8)).save(args.png)
    print(f"{size[0]} x {size[1]} px, {args.mm_per_pixel} mm per pixel")


if __name__ == "__main__":
    main()
