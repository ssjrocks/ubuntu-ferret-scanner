#!/usr/bin/env python3
"""Aligns a moving Y14 capture into one point cloud.

Uses point-to-plane ICP (Chen and Medioni) with projective matching (Newcombe et al.,
KinectFusion) against a keyframe. There is no loop closure yet, so drift is not corrected.
The lens centre is assumed to be mid-frame, and distortion is ignored.
"""
import argparse
import csv
import json

import numpy as np

from depth_to_cloud import BASELINE, FOCAL, FRAME_BYTES, HEIGHT, MODE_OFFSETS, UNIT, WIDTH, render, write_ply

DOWNSCALES = (8, 4)
ITERATIONS = 12
MATCH_MM = 12.0
MIN_NORMAL_COS = 0.7
KEYFRAME_MM = 25.0
KEYFRAME_RAD = 0.09
LOST_INLIERS = 0.25
VOXEL_MM = 1.5
MAX_POINT_FRAMES = 200


def decode_y14_fast(raw):
    groups = raw[: len(raw) // 7 * 7].reshape(-1, 7).astype(np.uint16)
    b = groups.T
    pixels = np.empty((groups.shape[0], 4), dtype=np.uint16)
    pixels[:, 0] = b[0] << 6 | b[1] >> 2
    pixels[:, 1] = (b[1] & 0x03) << 12 | b[2] << 4 | b[3] >> 4
    pixels[:, 2] = (b[3] & 0x0F) << 10 | b[4] << 2 | b[5] >> 6
    pixels[:, 3] = (b[5] & 0x3F) << 8 | b[6]
    return pixels.ravel().reshape(HEIGHT, WIDTH)


def to_depth_mm(values, offset):
    depth = np.full(values.shape, np.nan, dtype=np.float32)
    valid = values > 0
    depth[valid] = UNIT * BASELINE * FOCAL / (values[valid] / 64.0 + offset)
    return depth


def downscale(depth, factor):
    """Block median, ignoring pixels without depth."""
    h, w = depth.shape[0] // factor, depth.shape[1] // factor
    blocks = depth[: h * factor, : w * factor].reshape(h, factor, w, factor)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(h, w, -1)
    sorted_blocks = np.sort(blocks, axis=2)
    count = (~np.isnan(sorted_blocks)).sum(axis=2)
    out = np.full((h, w), np.nan, dtype=np.float32)
    enough = count >= factor * factor // 2
    rows, cols = np.nonzero(enough)
    out[rows, cols] = sorted_blocks[rows, cols, count[rows, cols] // 2]
    return out


class Level:
    """Vertex and normal maps for one image scale."""

    def __init__(self, depth, factor):
        self.h, self.w = depth.shape
        self.focal = FOCAL / factor
        self.cx, self.cy = (WIDTH / 2) / factor, (HEIGHT / 2) / factor
        v, u = np.mgrid[0 : self.h, 0 : self.w].astype(np.float32)
        x = (u - self.cx) * depth / self.focal
        y = (v - self.cy) * depth / self.focal
        self.vertices = np.dstack((x, y, depth))

        du = self.vertices[1:-1, 2:] - self.vertices[1:-1, :-2]
        dv = self.vertices[2:, 1:-1] - self.vertices[:-2, 1:-1]
        normals = np.cross(du, dv)
        length = np.linalg.norm(normals, axis=2, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            normals = normals / length
        self.normals = np.full(self.vertices.shape, np.nan, dtype=np.float32)
        self.normals[1:-1, 1:-1] = -normals
        self.valid = ~np.isnan(self.normals).any(axis=2)


def small_transform(delta):
    wx, wy, wz, tx, ty, tz = delta
    T = np.eye(4)
    T[:3, :3] = [[1, -wz, wy], [wz, 1, -wx], [-wy, wx, 1]]
    u, _, vt = np.linalg.svd(T[:3, :3])
    T[:3, :3] = u @ vt
    T[:3, 3] = (tx, ty, tz)
    return T


def icp_level(current, key, T):
    points = current.vertices[current.valid]
    normals_c = current.normals[current.valid]
    inliers, rms = 0.0, np.inf
    for _ in range(ITERATIONS):
        p = points @ T[:3, :3].T + T[:3, 3]
        n_c = normals_c @ T[:3, :3].T
        u = np.round(p[:, 0] * key.focal / p[:, 2] + key.cx).astype(np.int32)
        v = np.round(p[:, 1] * key.focal / p[:, 2] + key.cy).astype(np.int32)
        inside = (u >= 0) & (u < key.w) & (v >= 0) & (v < key.h) & (p[:, 2] > 0)
        index = np.nonzero(inside)[0]
        index = index[key.valid[v[index], u[index]]]
        if len(index) < 200:
            return T, 0.0, np.inf

        q = key.vertices[v[index], u[index]]
        n = key.normals[v[index], u[index]]
        pi = p[index]
        close = np.linalg.norm(pi - q, axis=1) < MATCH_MM
        aligned = (n * n_c[index]).sum(axis=1) > MIN_NORMAL_COS
        keep = close & aligned
        if keep.sum() < 200:
            return T, 0.0, np.inf

        pi, q, n = pi[keep], q[keep], n[keep]
        residual = ((pi - q) * n).sum(axis=1)
        scale = max(np.median(np.abs(residual)) * 1.5, 0.3)
        weight = np.minimum(1.0, scale / np.maximum(np.abs(residual), 1e-6))
        J = np.hstack((np.cross(pi, n), n))
        A = (J * weight[:, None]).T @ J
        b = (J * weight[:, None]).T @ -residual
        delta = np.linalg.solve(A + np.eye(6) * 1e-3, b)
        T = small_transform(delta) @ T
        inliers = keep.sum() / max(len(points), 1)
        rms = float(np.sqrt(np.mean(residual**2)))
        if np.abs(delta[:3]).max() < 1e-5 and np.abs(delta[3:]).max() < 1e-3:
            break
    return T, inliers, rms


def voxel_merge(sums, counts, points):
    keys = np.floor(points / VOXEL_MM).astype(np.int64)
    packed = (keys[:, 0] + 100000) * 400000**2 + (keys[:, 1] + 100000) * 400000 + keys[:, 2] + 100000
    unique, inverse = np.unique(packed, return_inverse=True)
    added = np.column_stack(
        [np.bincount(inverse, weights=points[:, i], minlength=len(unique)) for i in range(3)]
    )
    number = np.bincount(inverse, minlength=len(unique))
    for key, total, n in zip(unique.tolist(), added, number.tolist()):
        if key in sums:
            sums[key] += total
            counts[key] += n
        else:
            sums[key] = total.copy()
            counts[key] = n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix")
    parser.add_argument("--mode", choices=MODE_OFFSETS, default="long")
    parser.add_argument("--out", required=True, help="output path without an extension")
    parser.add_argument("--min-fill", type=float, default=0.05)
    args = parser.parse_args()

    offset = MODE_OFFSETS[args.mode]
    raw = np.memmap(args.prefix + ".raw", dtype=np.uint8, mode="r")
    frames = len(raw) // FRAME_BYTES

    poses, keyframe_pose, key_levels = [], np.eye(4), None
    T_key = np.eye(4)
    previous_step = np.eye(4)
    log, lost = [], 0
    sums, counts = {}, {}
    used = 0
    stride = max(frames // MAX_POINT_FRAMES, 1)
    for i in range(frames):
        values = decode_y14_fast(np.asarray(raw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]))
        depth = to_depth_mm(values, offset)
        fill = float(np.mean(~np.isnan(depth)))
        if fill < args.min_fill:
            log.append({"frame": i, "fill": fill, "state": "low depth"})
            continue

        levels = {f: Level(downscale(depth, f), f) for f in DOWNSCALES}
        if key_levels is None:
            key_levels = levels
            T_key, pose = np.eye(4), np.eye(4)
            poses.append((i, pose))
            log.append({"frame": i, "fill": fill, "state": "first"})
        else:
            guess = previous_step @ T_key
            T, inliers, rms = guess, 0.0, np.inf
            for f in DOWNSCALES:
                T, inliers, rms = icp_level(levels[f], key_levels[f], T)
                if inliers == 0.0:
                    break
            if inliers < LOST_INLIERS:
                lost += 1
                log.append({"frame": i, "fill": fill, "state": "lost", "inliers": inliers})
                previous_step = np.eye(4)
                continue

            previous_step = T @ np.linalg.inv(T_key)
            T_key = T
            pose = keyframe_pose @ T_key
            poses.append((i, pose))
            log.append({"frame": i, "fill": fill, "state": "ok", "inliers": float(inliers),
                        "rms": rms, "pose_t": pose[:3, 3].round(2).tolist()})

            angle = np.arccos(np.clip((np.trace(T_key[:3, :3]) - 1) / 2, -1, 1))
            if np.linalg.norm(T_key[:3, 3]) > KEYFRAME_MM or angle > KEYFRAME_RAD:
                keyframe_pose, key_levels, T_key = pose, levels, np.eye(4)

        if i % stride == 0:
            rows, cols = np.nonzero(~np.isnan(depth))
            rows, cols = rows[::2], cols[::2]
            z = depth[rows, cols]
            p = np.column_stack(((cols - WIDTH / 2) * z / FOCAL, (rows - HEIGHT / 2) * z / FOCAL, z))
            voxel_merge(sums, counts, p @ pose[:3, :3].T + pose[:3, 3])
            used += 1
        if i % 100 == 0:
            print(f"frame {i}/{frames}, poses {len(poses)}, lost {lost}, voxels {len(sums)}", flush=True)

    with open(args.out + "-log.json", "w") as f:
        json.dump(log, f)
    with open(args.out + "-poses.csv", "w", newline="") as f:
        writer = csv.writer(f)
        for frame, pose in poses:
            writer.writerow([frame, *pose.ravel().tolist()])

    if not sums:
        raise SystemExit("no frames could be aligned")
    # Flip y back to up for viewing: maps use y down.
    number = np.array([counts[k] for k in sums], dtype=np.float32)
    points = np.array(list(sums.values()), dtype=np.float32) / number[:, None]
    points = points[number >= 2]
    points[:, 1] *= -1
    write_ply(args.out + ".ply", points)
    print(f"{frames} frames, aligned {len(poses)}, lost {lost}, used {used}, {len(points)} points")
    for name, yaw in (("front", 0.0), ("left", -0.75), ("right", 0.75)):
        render(points, yaw, 0.2, f"{args.out}-{name}.png")


if __name__ == "__main__":
    main()
