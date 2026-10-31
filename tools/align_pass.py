#!/usr/bin/env python3
"""Aligns a moving Y14 capture into one point cloud.

Uses point-to-plane ICP (Chen and Medioni) with projective matching (Newcombe et al.,
KinectFusion) against a keyframe, then pose graph loop closure when the path revisits
earlier keyframes. The lens centre is assumed to be mid-frame, and distortion is ignored.
"""
import argparse
import csv
import json

import numpy as np

from pose_graph import edge_error, optimize
from depth_to_cloud import BASELINE, FOCAL, FRAME_BYTES, HEIGHT, MODE_OFFSETS, UNIT, WIDTH, render, write_ply

DOWNSCALES = (8, 4)
ITERATIONS = 12
MATCH_MM = 12.0
MIN_NORMAL_COS = 0.7
KEYFRAME_MM = 25.0
KEYFRAME_RAD = 0.09
LOST_INLIERS = 0.25
VOXEL_MM = 1.5
LOOP_GAP_KEYFRAMES = 15
LOOP_DISTANCE_MM = 150.0
LOOP_ANGLE_RAD = 0.5
LOOP_CANDIDATES = 3
LOOP_SEARCH_MM = 40.0
LOOP_INLIERS = 0.45
LOOP_RMS_MM = 1.5
LOOP_MIN_CONDITION = 0.02
LOOP_WEIGHT = 10.0
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


def icp_level(current, key, T, match_mm=MATCH_MM):
    points = current.vertices[current.valid]
    normals_c = current.normals[current.valid]
    inliers, rms = 0.0, np.inf
    condition = 0.0
    for _ in range(ITERATIONS):
        p = points @ T[:3, :3].T + T[:3, 3]
        n_c = normals_c @ T[:3, :3].T
        u = np.round(p[:, 0] * key.focal / p[:, 2] + key.cx).astype(np.int32)
        v = np.round(p[:, 1] * key.focal / p[:, 2] + key.cy).astype(np.int32)
        inside = (u >= 0) & (u < key.w) & (v >= 0) & (v < key.h) & (p[:, 2] > 0)
        index = np.nonzero(inside)[0]
        index = index[key.valid[v[index], u[index]]]
        if len(index) < 200:
            return T, 0.0, np.inf, 0.0

        q = key.vertices[v[index], u[index]]
        n = key.normals[v[index], u[index]]
        pi = p[index]
        close = np.linalg.norm(pi - q, axis=1) < match_mm
        aligned = (n * n_c[index]).sum(axis=1) > MIN_NORMAL_COS
        keep = close & aligned
        if keep.sum() < 200:
            return T, 0.0, np.inf, 0.0

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
        # A flat scene can slide freely, so weak translation directions make a match unsafe.
        strength = np.linalg.eigvalsh(A[3:, 3:])
        condition = float(strength[0] / strength[-1])
        if np.abs(delta[:3]).max() < 1e-5 and np.abs(delta[3:]).max() < 1e-3:
            break
    return T, inliers, rms, condition


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


def relative_angle(T):
    return np.arccos(np.clip((np.trace(T[:3, :3]) - 1) / 2, -1, 1))


def find_loops(keyframes, newest):
    """Matches the newest keyframe against earlier ones close to it on the current path."""
    latest = keyframes[newest]
    scored = []
    for i in range(newest - LOOP_GAP_KEYFRAMES):
        relative = np.linalg.inv(keyframes[i]["pose"]) @ latest["pose"]
        distance = np.linalg.norm(relative[:3, 3])
        if distance < LOOP_DISTANCE_MM and relative_angle(relative) < LOOP_ANGLE_RAD:
            scored.append((distance, i, relative))

    loops = []
    for _, i, relative in sorted(scored, key=lambda item: item[0])[:LOOP_CANDIDATES]:
        T = relative
        for factor in DOWNSCALES:
            T, inliers, rms, condition = icp_level(
                latest["levels"][factor], keyframes[i]["levels"][factor], T, LOOP_SEARCH_MM
            )
            if inliers == 0.0:
                break
        T, inliers, rms, condition = icp_level(
            latest["levels"][DOWNSCALES[-1]], keyframes[i]["levels"][DOWNSCALES[-1]], T
        )
        if inliers >= LOOP_INLIERS and rms <= LOOP_RMS_MM and condition >= LOOP_MIN_CONDITION:
            loops.append((i, newest, T))
    return loops


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

    keyframes, edges, loops = [], [], []
    frame_poses = {}  # frame: (keyframe index, transform from frame to keyframe)
    T_key = np.eye(4)
    previous_step = np.eye(4)
    log, lost = [], 0
    for i in range(frames):
        values = decode_y14_fast(np.asarray(raw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]))
        depth = to_depth_mm(values, offset)
        fill = float(np.mean(~np.isnan(depth)))
        if fill < args.min_fill:
            log.append({"frame": i, "fill": fill, "state": "low depth"})
            continue

        levels = {f: Level(downscale(depth, f), f) for f in DOWNSCALES}
        if not keyframes:
            keyframes.append({"frame": i, "pose": np.eye(4), "levels": levels})
            frame_poses[i] = (0, np.eye(4))
            log.append({"frame": i, "fill": fill, "state": "first"})
            continue

        guess = previous_step @ T_key
        T, inliers, rms = guess, 0.0, np.inf
        for f in DOWNSCALES:
            T, inliers, rms, _ = icp_level(levels[f], keyframes[-1]["levels"][f], T)
            if inliers == 0.0:
                break
        if inliers < LOST_INLIERS:
            lost += 1
            log.append({"frame": i, "fill": fill, "state": "lost", "inliers": inliers})
            previous_step = np.eye(4)
            continue

        previous_step = T @ np.linalg.inv(T_key)
        T_key = T
        frame_poses[i] = (len(keyframes) - 1, T_key.copy())
        log.append({"frame": i, "fill": fill, "state": "ok", "inliers": float(inliers), "rms": rms})

        if np.linalg.norm(T_key[:3, 3]) > KEYFRAME_MM or relative_angle(T_key) > KEYFRAME_RAD:
            pose = keyframes[-1]["pose"] @ T_key
            edges.append((len(keyframes) - 1, len(keyframes), T_key.copy(), 1.0))
            keyframes.append({"frame": i, "pose": pose, "levels": levels})
            frame_poses[i] = (len(keyframes) - 1, np.eye(4))
            T_key = np.eye(4)
            loops.extend(find_loops(keyframes, len(keyframes) - 1))
        if i % 100 == 0:
            print(f"frame {i}/{frames}, keyframes {len(keyframes)}, loops {len(loops)}, lost {lost}",
                  flush=True)

    tracked = [k["pose"] for k in keyframes]
    gaps = []
    for i, j, measured in loops:
        error = edge_error(tracked[i], tracked[j], measured)
        gaps.append((i, j, float(np.linalg.norm(error[3:])), float(np.degrees(np.linalg.norm(error[:3])))))
    corrected = tracked
    if loops:
        corrected = optimize(tracked, edges + [(i, j, m, LOOP_WEIGHT) for i, j, m in loops])

    with open(args.out + "-log.json", "w") as f:
        json.dump({"frames": log, "loops": gaps}, f)
    with open(args.out + "-poses.csv", "w", newline="") as f:
        writer = csv.writer(f)
        for frame, (key, T) in sorted(frame_poses.items()):
            writer.writerow([frame, *(corrected[key] @ T).ravel().tolist()])

    sums, counts = {}, {}
    chosen = sorted(frame_poses)[:: max(len(frame_poses) // MAX_POINT_FRAMES, 1)]
    for i in chosen:
        key, T = frame_poses[i]
        pose = corrected[key] @ T
        values = decode_y14_fast(np.asarray(raw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]))
        depth = to_depth_mm(values, offset)
        rows, cols = np.nonzero(~np.isnan(depth))
        rows, cols = rows[::2], cols[::2]
        z = depth[rows, cols]
        p = np.column_stack(((cols - WIDTH / 2) * z / FOCAL, (rows - HEIGHT / 2) * z / FOCAL, z))
        voxel_merge(sums, counts, p @ pose[:3, :3].T + pose[:3, 3])

    # Flip y back to up for viewing: maps use y down.
    number = np.array([counts[k] for k in sums], dtype=np.float32)
    points = np.array(list(sums.values()), dtype=np.float32) / number[:, None]
    points = points[number >= 2]
    points[:, 1] *= -1
    write_ply(args.out + ".ply", points)
    print(f"{frames} frames, aligned {len(frame_poses)}, lost {lost}, keyframes {len(keyframes)}")
    print(f"loops {len(loops)}, used {len(chosen)} frames, {len(points)} points")
    if gaps:
        print(f"loop gaps: largest {max(g[2] for g in gaps):.1f} mm, {max(g[3] for g in gaps):.2f} deg")
    for name, yaw in (("front", 0.0), ("left", -0.75), ("right", 0.75)):
        render(points, yaw, 0.2, f"{args.out}-{name}.png")


if __name__ == "__main__":
    main()
