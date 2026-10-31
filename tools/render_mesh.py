#!/usr/bin/env python3
"""Draws a model's mesh with plain shading from a chosen direction, without a window."""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ubuntu_ferret_scanner import ply

SIZE = 1400


def render(vertices, normals, colours, yaw, pitch):
    centre = np.median(vertices, axis=0)
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rotation = Rx @ Ry
    p = (vertices - centre) @ rotation.T
    n = normals @ rotation.T

    low, high = np.percentile(p[:, :2], 0.2, axis=0), np.percentile(p[:, :2], 99.8, axis=0)
    scale = SIZE * 0.95 / (high - low).max()
    u = ((p[:, 0] - low[0]) * scale).astype(int)
    v = ((high[1] - p[:, 1]) * scale).astype(int)
    keep = (u >= 0) & (u < SIZE - 1) & (v >= 0) & (v < SIZE - 1)
    light = np.abs(n @ (np.array([0.3, 0.5, 0.8]) / 1.03))
    order = np.argsort(p[keep, 2])
    if colours is None:
        picture = np.zeros((SIZE, SIZE), dtype=np.uint8)
        value = (40 + 215 * light[keep][order]).astype(np.uint8)
    else:
        picture = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
        value = colours[keep][order]
    for du, dv in ((0, 0), (1, 0), (0, 1), (1, 1)):
        picture[v[keep][order] + dv, u[keep][order] + du] = value
    return picture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh")
    parser.add_argument("out")
    parser.add_argument("--yaw", type=float, default=0.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--colour", action="store_true")
    args = parser.parse_args()
    vertices, normals, colours, _ = ply.read_mesh(args.mesh)
    picture = render(vertices, normals, colours if args.colour else None, args.yaw, args.pitch)
    Image.fromarray(picture).save(args.out)


if __name__ == "__main__":
    main()
