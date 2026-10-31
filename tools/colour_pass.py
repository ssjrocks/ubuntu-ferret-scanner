#!/usr/bin/env python3
"""Colours an aligned pass from the colour video recorded alongside it.

Depth and colour have separate clocks, so the time shift is found by testing which shift
lands depth edges on colour edges in frames where the scanner was moving fastest.
"""
import argparse
import csv
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from align_pass import VOXEL_MM, decode_y14_fast, to_depth_mm
from colour_cloud import project_to_colour, write_colour_ply
from depth_to_cloud import CENTRE_X, CENTRE_Y, FOCAL, FRAME_BYTES, MODE_OFFSETS

COLOUR_SIZE = (1920, 1080)
EDGE_JUMP_MM = 12.0
SYNC_FRAMES = 24
SYNC_RANGE_MS = 2000
SYNC_STEP_MS = 33
COLOUR_MARGIN = 80
MIN_VOTES = 2
VIEW_MM_PER_PIXEL = 1.5


def extract_frames(path, folder):
    """Copies each colour frame out unchanged, so frame numbers match the video's packets."""
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-c", "copy",
                    "-fps_mode", "passthrough", str(folder / "%05d.jpg")], check=True)
    result = subprocess.run(["ffprobe", "-hide_banner", "-loglevel", "error", "-select_streams", "v",
                             "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                            check=True, capture_output=True, text=True)
    times = [float(line.strip().rstrip(",")) * 1000 for line in result.stdout.split() if line.strip()]
    return np.array(times)


def load_edges(path):
    """Edge strength at half size, or None for a frame that won't decode."""
    try:
        gray = Image.open(path).convert("L").resize((960, 540))
        a = np.asarray(gray, dtype=np.float32)
    except OSError:
        return None
    edges = np.abs(np.gradient(a, axis=0)) + np.abs(np.gradient(a, axis=1))
    blurred = Image.fromarray(np.clip(edges * 3, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(1))
    return np.asarray(blurred, dtype=np.float32)


def depth_edges(depth):
    """Points on the near side of depth jumps, in depth camera space."""
    z = np.nan_to_num(depth, nan=1e4)
    jump = np.zeros(z.shape, dtype=bool)
    jump[:, :-1] |= (np.abs(np.diff(z, axis=1)) > EDGE_JUMP_MM) & (z[:, :-1] < z[:, 1:])
    jump[:, 1:] |= (np.abs(np.diff(z, axis=1)) > EDGE_JUMP_MM) & (z[:, 1:] < z[:, :-1])
    jump[:-1, :] |= (np.abs(np.diff(z, axis=0)) > EDGE_JUMP_MM) & (z[:-1, :] < z[1:, :])
    jump[1:, :] |= (np.abs(np.diff(z, axis=0)) > EDGE_JUMP_MM) & (z[1:, :] < z[:-1, :])
    rows, cols = np.nonzero(jump & ~np.isnan(depth))
    d = depth[rows, cols]
    return np.column_stack(((cols - CENTRE_X) * d / FOCAL, (rows - CENTRE_Y) * d / FOCAL, d))


def find_offset(raw, depth_ms, colour_ms, folder, frames, offset, poses):
    """Finds the shift added to colour timestamps that best lands depth edges on colour edges."""
    speed = {}
    ordered = sorted(poses)
    for a, b in zip(ordered, ordered[1:]):
        speed[b] = float(np.linalg.norm(poses[b][:3, 3] - poses[a][:3, 3]))
    fastest = sorted(speed, key=speed.get, reverse=True)[:: max(len(speed) // (SYNC_FRAMES * 3), 1)]
    sample = sorted(fastest[:SYNC_FRAMES])

    prepared = []
    for i in sample:
        values = decode_y14_fast(np.asarray(raw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]))
        edges = depth_edges(to_depth_mm(values, offset))
        if len(edges) < 500:
            continue
        u, v = project_to_colour(edges)
        c, r = np.round(u / 2).astype(int), np.round(v / 2).astype(int)
        keep = (c >= 0) & (c < 960) & (r >= 0) & (r < 540)
        prepared.append((i, r[keep], c[keep]))

    cache, scores = {}, {}
    for shift in range(-SYNC_RANGE_MS, SYNC_RANGE_MS + 1, SYNC_STEP_MS):
        total = []
        for i, r, c in prepared:
            n = int(np.argmin(np.abs(colour_ms + shift - depth_ms[i])))
            if n not in cache:
                cache[n] = load_edges(folder / f"{n + 1:05d}.jpg")
            if cache[n] is not None:
                total.append(cache[n][r, c].mean())
        scores[shift] = float(np.mean(total))
    return scores


def view(points, colours, path):
    centred = points - np.median(points, axis=0)
    axes = np.linalg.svd(centred[:: max(len(points) // 200000, 1)], full_matrices=False)[2]
    flat = centred @ axes.T
    low = np.percentile(flat[:, :2], 0.2, axis=0)
    high = np.percentile(flat[:, :2], 99.8, axis=0)
    size = np.ceil((high - low) / VIEW_MM_PER_PIXEL).astype(int) + 1
    cols, rows = ((flat[:, :2] - low) / VIEW_MM_PER_PIXEL).astype(int).T
    inside = (cols >= 0) & (cols < size[0]) & (rows >= 0) & (rows < size[1])
    picture = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    picture[rows[inside], cols[inside]] = colours[inside]
    Image.fromarray(picture).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", help="raw capture path without .raw")
    parser.add_argument("colour", help="colour video")
    parser.add_argument("poses", help="poses CSV from align_pass.py")
    parser.add_argument("--mode", choices=MODE_OFFSETS, default="long")
    parser.add_argument("--out", required=True, help="output path without an extension")
    parser.add_argument("--stride", type=int, default=4)
    args = parser.parse_args()

    offset = MODE_OFFSETS[args.mode]
    raw = np.memmap(args.prefix + ".raw", dtype=np.uint8, mode="r")
    frames = len(raw) // FRAME_BYTES
    poses = {}
    with open(args.poses) as f:
        for row in csv.reader(f):
            poses[int(row[0])] = np.array(row[1:], dtype=float).reshape(4, 4)

    with open(args.prefix + ".csv") as f:
        rows_csv = list(csv.DictReader(f))
    depth_ms = np.array([float(r["host_ms"]) for r in rows_csv])

    with tempfile.TemporaryDirectory(dir=Path(args.out).parent, prefix="colour-frames-") as scratch:
        folder = Path(scratch)
        colour_ms = extract_frames(args.colour, folder)
        scores = find_offset(raw, depth_ms, colour_ms, folder, frames, offset, poses)
        shift = max(scores, key=scores.get)
        values = np.array(list(scores.values()))
        print(f"time shift {shift} ms, score {scores[shift]:.2f}, typical {np.median(values):.2f}, "
              f"spread {values.std():.2f}", flush=True)
        if abs(shift) >= SYNC_RANGE_MS - SYNC_STEP_MS:
            raise SystemExit("the best shift is at the edge of the search, so the sync can't be trusted")

        all_points, all_colours = [], []
        for i in sorted(poses)[:: args.stride]:
            n = int(np.argmin(np.abs(colour_ms + shift - depth_ms[i])))
            try:
                picture = np.asarray(Image.open(folder / f"{n + 1:05d}.jpg").convert("RGB"))
            except OSError:
                continue
            depth = to_depth_mm(decode_y14_fast(np.asarray(raw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES])), offset)
            rows, cols = np.nonzero(~np.isnan(depth))
            rows, cols = rows[::2], cols[::2]
            z = depth[rows, cols]
            p = np.column_stack(((cols - CENTRE_X) * z / FOCAL, (rows - CENTRE_Y) * z / FOCAL, z))
            u, v = project_to_colour(p)
            u, v = np.round(u).astype(int), np.round(v).astype(int)
            seen = (u >= COLOUR_MARGIN) & (u < COLOUR_SIZE[0] - COLOUR_MARGIN)
            seen &= (v >= COLOUR_MARGIN) & (v < COLOUR_SIZE[1] - COLOUR_MARGIN)
            pose = poses[i]
            all_points.append((p[seen] @ pose[:3, :3].T + pose[:3, 3]).astype(np.float32))
            all_colours.append(picture[v[seen], u[seen]])

    points = np.concatenate(all_points)
    colours = np.concatenate(all_colours).astype(np.float32)
    del all_points, all_colours

    keys = np.floor(points / VOXEL_MM).astype(np.int64)
    packed = (keys[:, 0] + 100000) * 400000**2 + (keys[:, 1] + 100000) * 400000 + keys[:, 2] + 100000
    _, inverse, votes = np.unique(packed, return_inverse=True, return_counts=True)
    merged = np.column_stack(
        [np.bincount(inverse, weights=c) / votes for c in (*points.T, *colours.T)]
    )
    merged = merged[votes >= MIN_VOTES]
    final_points = merged[:, :3].astype(np.float32)
    final_points[:, 1] *= -1
    final_colours = np.clip(merged[:, 3:], 0, 255).astype(np.uint8)
    write_colour_ply(args.out + ".ply", final_points, final_colours)
    view(final_points.astype(np.float64), final_colours, args.out + ".png")
    print(f"{len(final_points)} coloured points")


if __name__ == "__main__":
    main()
