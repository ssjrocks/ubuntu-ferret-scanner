"""Builds a coloured point cloud and a mesh from a recorded session.

Runs as its own process so the app stays responsive. Each line of stdout is a JSON message.
Fusion is a truncated signed distance field (Curless and Levoy), meshed by marching cubes.
"""
import argparse
import io
import json
import sys
import time

import numpy as np
import torch
from PIL import Image, ImageFilter
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from skimage import measure

from ubuntu_ferret_scanner.calibration import (
    CENTRE_X,
    CENTRE_Y,
    COLOUR_SIZE,
    DEPTH_BYTES,
    EDGE_WINDOW,
    FOCAL,
    HEIGHT,
    MODE_OFFSETS,
    WIDTH,
    decode_y14,
    project_to_colour,
    remove_edges,
    to_depth_mm,
)
from ubuntu_ferret_scanner.pose_graph import edge_error, optimize
from ubuntu_ferret_scanner.ply import camera_to_view, write_cloud, write_mesh, write_stl
from ubuntu_ferret_scanner.relocalise import Relocaliser, decode_jpeg, describe
from ubuntu_ferret_scanner.session import ColourFrames, Session, read_index
from ubuntu_ferret_scanner.settings import Settings, device_for
from ubuntu_ferret_scanner.tracking import DOWNSCALES, Tracker, icp_level

LOOP_GAP_KEYFRAMES = 6
LOOP_DISTANCE_MM = 150.0
LOOP_ANGLE_RAD = 0.5
LOOP_SEARCH_MM = 40.0
LOOP_INLIERS = 0.45
LOOP_RMS_MM = 1.5
LOOP_MIN_CONDITION = 0.02
LOOP_WEIGHT = 1.0
REFINE_SEARCH_MM = (40.0, 25.0, 15.0, 10.0)
REFINE_CANDIDATES = 6
MAX_LOOP_ERROR_MM = 6.0
MAX_LOOP_ERROR_RAD = 0.02
NEW_SEGMENT_FRAMES = 6
LINK_AGREEMENT = 2
LINK_AGREE_MM = 15.0
LINK_AGREE_RAD = 0.08

CLOUD_VOXEL_MM = 1.5
MERGE_BATCH_POINTS = 30_000_000
CLOUD_PIXEL_STEP = 2
COLOUR_MARGIN = 80
MIN_VOTES = 2

EDGE_JUMP_MM = 12.0
SYNC_FRAMES = 24
SYNC_RANGE_MS = 198  # app recordings share one clock, so the true shift is small
SYNC_STEP_MS = 33

BYTES_PER_VOXEL = 24
VOLUME_SHARE = 0.6  # the rest is working memory for each frame
MIN_VOXEL_MM = 0.5
BRICK_VOXELS = 16
CHUNK_BRICKS = 16
TRUNCATION_MM = 3.0
MIN_TRUNCATION_VOXELS = 3
MIN_WEIGHT = 6.0
MIN_PIECE_FACES = 300
COLOUR_BAND_VOXELS = 1.5
MIN_FACING = 0.25
GAIN_LIMITS = (0.6, 1.6)
GAIN_SAMPLES = 20000


def say(stage, progress=None, **extra):
    print(json.dumps({"stage": stage, "progress": progress, **extra}), flush=True)


class DepthFrames:
    def __init__(self, session, offset, device):
        self.file = open(session.depth_path, "rb")
        self.offset = offset
        self.device = device

    def read(self, index):
        self.file.seek(index * DEPTH_BYTES)
        data = self.file.read(DEPTH_BYTES)
        raw = torch.frombuffer(bytearray(data), dtype=torch.uint8).to(self.device)
        return to_depth_mm(decode_y14(raw), self.offset)

    def read_clean(self, index):
        return remove_edges(self.read(index))


def match_keyframes(a, b, guess, search_mm):
    """Refines the transform from keyframe b to a. Returns it if the match is trustworthy."""
    T = guess
    finest = DOWNSCALES[-1]
    for factor in DOWNSCALES:
        T, inliers, _, _ = icp_level(b["levels"][factor], a["levels"][factor], T, search_mm)
        if inliers == 0.0:
            return None
    T, inliers, rms, condition = icp_level(b["levels"][finest], a["levels"][finest], T)
    if inliers >= LOOP_INLIERS and rms <= LOOP_RMS_MM and condition >= LOOP_MIN_CONDITION:
        return T
    return None


def overlapping(keyframes, poses, j, limit):
    """Earlier keyframes likely to see the same surfaces as keyframe j, nearest first."""
    scored = []
    for i in range(j - LOOP_GAP_KEYFRAMES):
        relative = np.linalg.inv(poses[i]) @ poses[j]
        distance = float(np.linalg.norm(relative[:3, 3]))
        angle = float(np.arccos(np.clip((np.trace(relative[:3, :3]) - 1) / 2, -1, 1)))
        if distance < LOOP_DISTANCE_MM and angle < LOOP_ANGLE_RAD:
            scored.append((distance / LOOP_DISTANCE_MM + angle / LOOP_ANGLE_RAD, i, relative))
    return sorted(scored, key=lambda item: item[0])[:limit]


def refine(keyframes, edges, device):
    """Repeatedly matches overlapping keyframes and corrects the whole path.

    Each round uses the corrected path to find more overlaps with a tighter search, so
    multi-lap scans pull together. Matches the solver can't agree with are dropped.
    """
    poses = [k["pose"].cpu().numpy() for k in keyframes]
    first_gap, loops = 0.0, []
    for number, search_mm in enumerate(REFINE_SEARCH_MM):
        say("Refining alignment", number / len(REFINE_SEARCH_MM))
        loops = []
        for j in range(len(keyframes)):
            for _, i, relative in overlapping(keyframes, poses, j, REFINE_CANDIDATES):
                guess = torch.from_numpy(relative).to(device)
                T = match_keyframes(keyframes[i], keyframes[j], guess, search_mm)
                if T is not None:
                    loops.append((i, j, T.cpu().numpy()))
        if not loops:
            break
        gaps = [float(np.linalg.norm(edge_error(poses[i], poses[j], m)[3:])) for i, j, m in loops]
        if number == 0:
            first_gap = max(gaps)
        graph = edges + [(i, j, m, LOOP_WEIGHT) for i, j, m in loops]
        solved = optimize(poses, graph)
        kept = [loop for loop in loops if outlier_free(solved, loop)]
        poses = optimize(poses, edges + [(i, j, m, LOOP_WEIGHT) for i, j, m in kept])
        loops = kept
    remaining = [float(np.linalg.norm(edge_error(poses[i], poses[j], m)[3:])) for i, j, m in loops]
    return poses, {"loops": len(loops), "largest_gap_mm": first_gap, "final_gap_mm": max(remaining, default=0.0)}


def outlier_free(poses, loop):
    i, j, measured = loop
    error = edge_error(poses[i], poses[j], measured)
    return np.linalg.norm(error[3:]) < MAX_LOOP_ERROR_MM and np.linalg.norm(error[:3]) < MAX_LOOP_ERROR_RAD


def add_keyframe(keyframes, relocaliser, colour, depth_ms, frame, depth, levels, pose, segment):
    view = None
    picture = None
    if colour is not None:
        index = colour.nearest(depth_ms[frame])
        picture = decode_jpeg(colour.read(index))
    if picture is not None:
        view = describe(depth, picture)
    number = len(keyframes)
    keyframes.append({"pose": pose, "levels": levels, "segment": segment, "view": view})
    if view is not None:
        relocaliser.add(number, view, levels)
    return number


def link_segments(keyframes, relocaliser, edges):
    """Places separated pieces of the scan together using shared views.

    Returns the world transform of each joined segment. Two matches must agree before a piece
    is joined, because a piece joined in the wrong place would ruin the whole model.
    """
    members = {}
    for number, key in enumerate(keyframes):
        members.setdefault(key["segment"], []).append(number)
    main = max(members, key=lambda segment: len(members[segment]))
    placed = {main: np.eye(4)}

    def pose_of(key):
        return keyframes[key]["pose"].cpu().numpy()

    def try_join(segment, estimates):
        """Joins a segment once enough estimates of its position agree."""
        link = estimates[-1][0]
        agreeing = [e for e in estimates if transforms_agree(e[0], link)]
        if len(agreeing) < LINK_AGREEMENT:
            return False
        placed[segment] = link
        edges.extend(e[1] for e in agreeing)
        return True

    changed = True
    while changed and len(placed) < len(members):
        changed = False
        ready = {k for segment in placed for k in members[segment] if keyframes[k]["view"] is not None}
        for segment, keys in members.items():
            if segment in placed:
                continue
            say("Rejoining scan pieces", len(placed) / len(members))
            estimates = []
            options = {k for k in keys if keyframes[k]["view"] is not None}
            # Matching isn't symmetrical, so search from both sides.
            for k in sorted(options):
                match = relocaliser.find(keyframes[k]["view"], keyframes[k]["levels"], ready)
                if match is None:
                    continue
                other = placed[keyframes[match.key]["segment"]] @ pose_of(match.key)
                T = match.T.cpu().numpy()
                estimates.append((other @ T @ np.linalg.inv(pose_of(k)), (match.key, k, T, 1.0)))
                changed = try_join(segment, estimates)
                if changed:
                    break
            if changed:
                continue
            for k in sorted(ready):
                match = relocaliser.find(keyframes[k]["view"], keyframes[k]["levels"], options)
                if match is None:
                    continue
                here = placed[keyframes[k]["segment"]] @ pose_of(k)
                T = match.T.cpu().numpy()
                link = here @ np.linalg.inv(T) @ np.linalg.inv(pose_of(match.key))
                estimates.append((link, (match.key, k, T, 1.0)))
                changed = try_join(segment, estimates)
                if changed:
                    break
    return placed


def transforms_agree(a, b):
    difference = np.linalg.inv(a) @ b
    angle = np.arccos(np.clip((np.trace(difference[:3, :3]) - 1) / 2, -1, 1))
    return np.linalg.norm(difference[:3, 3]) < LINK_AGREE_MM and angle < LINK_AGREE_RAD


def align(depth_frames, colour, depth_ms, count, device):
    """Tracks every frame and corrects the path. Returns {frame: 4x4 pose} and stats."""
    tracker = Tracker(device)
    relocaliser = Relocaliser(device)
    keyframes, edges, placed = [], [], {}
    lost, lost_run, segment = 0, 0, 0
    current = None
    identity = torch.eye(4, dtype=torch.float64, device=device)
    for i in range(count):
        depth = depth_frames.read(i)
        result, levels = tracker.feed(depth)
        if result.state == "lost":
            lost += 1
            lost_run += 1
            # Stop waiting and start a new piece, which can be joined up after tracking.
            if lost_run >= NEW_SEGMENT_FRAMES and current is not None:
                tracker = Tracker(device)
                segment += 1
                current = None
            continue

        lost_run = 0
        if result.state == "first" or result.new_keyframe:
            number = add_keyframe(
                keyframes, relocaliser, colour, depth_ms, i, depth, levels, result.pose, segment
            )
            if current is not None:
                relative = torch.linalg.inv(keyframes[current]["pose"]) @ result.pose
                edges.append((current, number, relative.cpu().numpy(), 1.0))
            current = number
            placed[i] = (current, identity)
        else:
            placed[i] = (current, tracker.T_key.clone())
        if i % 20 == 0:
            say("Aligning frames", i / count)

    segments = link_segments(keyframes, relocaliser, edges)
    keep = [n for n, key in enumerate(keyframes) if key["segment"] in segments]
    renumber = {old: new for new, old in enumerate(keep)}
    joined = []
    for n in keep:
        key = dict(keyframes[n])
        world = segments[key["segment"]]
        key["pose"] = torch.from_numpy(world @ key["pose"].cpu().numpy()).to(device)
        joined.append(key)
    edges = [(renumber[a], renumber[b], m, w) for a, b, m, w in edges if a in renumber and b in renumber]

    corrected, stats = refine(joined, edges, device)
    poses = {i: corrected[renumber[key]] @ T.cpu().numpy() for i, (key, T) in placed.items() if key in renumber}
    return poses, {
        "lost": lost,
        "pieces": len({key["segment"] for key in keyframes}),
        "pieces_joined": len(segments),
        "unjoined_frames": len(placed) - len(poses),
        "keyframes": len(joined),
        **stats,
    }


def load_edges(data):
    try:
        gray = Image.open(io.BytesIO(data)).convert("L").resize((960, 540))
    except OSError:
        return None
    a = np.asarray(gray, dtype=np.float32)
    edges = np.abs(np.gradient(a, axis=0)) + np.abs(np.gradient(a, axis=1))
    blurred = Image.fromarray(np.clip(edges * 3, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(1))
    return np.asarray(blurred, dtype=np.float32)


def depth_edges(depth):
    """Points on the near side of depth jumps, in depth camera space."""
    z = depth.nan_to_num(nan=1e4)
    jump = torch.zeros_like(z, dtype=torch.bool)
    dx, dy = (z[:, :-1] - z[:, 1:]), (z[:-1, :] - z[1:, :])
    jump[:, :-1] |= dx < -EDGE_JUMP_MM
    jump[:, 1:] |= dx > EDGE_JUMP_MM
    jump[:-1, :] |= dy < -EDGE_JUMP_MM
    jump[1:, :] |= dy > EDGE_JUMP_MM
    rows, cols = (jump & ~depth.isnan()).nonzero(as_tuple=True)
    d = depth[rows, cols]
    return torch.stack(((cols - CENTRE_X) * d / FOCAL, (rows - CENTRE_Y) * d / FOCAL, d), dim=1)


def find_time_shift(depth_frames, depth_ms, colour, poses):
    """Finds the shift to add to colour times, by landing depth edges on colour edges."""
    ordered = sorted(poses)
    # Only neighbouring frames: a jump between frames either side of a loss isn't real speed.
    speed = {
        b: float(np.linalg.norm(poses[b][:3, 3] - poses[a][:3, 3]))
        for a, b in zip(ordered, ordered[1:])
        if b == a + 1
    }
    fastest = sorted(speed, key=speed.get, reverse=True)[: SYNC_FRAMES * 3]
    prepared = []
    for i in sorted(fastest)[:: 3]:
        edges = depth_edges(depth_frames.read(i))
        if edges.shape[0] < 500:
            continue
        u, v = project_to_colour(edges)
        c, r = (u / 2).round().long(), (v / 2).round().long()
        keep = (c >= 0) & (c < 960) & (r >= 0) & (r < 540)
        prepared.append((i, r[keep].cpu().numpy(), c[keep].cpu().numpy()))

    cache, scores = {}, {}
    for shift in range(-SYNC_RANGE_MS, SYNC_RANGE_MS + 1, SYNC_STEP_MS):
        totals = []
        for i, r, c in prepared:
            n = colour.nearest(depth_ms[i] - shift)
            if n not in cache:
                cache[n] = load_edges(colour.read(n))
            if cache[n] is not None:
                totals.append(cache[n][r, c].mean())
        if totals:
            scores[shift] = float(np.mean(totals))
    if not scores:
        return 0.0
    best = max(scores, key=scores.get)
    # An edge result means the true shift is outside the search, so keep the recorded timing.
    return 0.0 if abs(best) >= SYNC_RANGE_MS - SYNC_STEP_MS else float(best)


def decode_colour(colour, index, device):
    try:
        picture = np.asarray(Image.open(io.BytesIO(colour.read(index))).convert("RGB"))
    except OSError:
        return None
    return torch.from_numpy(picture.copy()).to(device)


def frame_colours(camera, picture):
    """Colours for camera-space points, and which points the colour camera saw."""
    u, v = project_to_colour(camera)
    u, v = u.round().long(), v.round().long()
    seen = (u >= COLOUR_MARGIN) & (u < COLOUR_SIZE[0] - COLOUR_MARGIN)
    seen &= (v >= COLOUR_MARGIN) & (v < COLOUR_SIZE[1] - COLOUR_MARGIN)
    return picture[v[seen], u[seen]].float(), seen


def camera_points(depth, step):
    rows, cols = (~depth.isnan()).nonzero(as_tuple=True)
    rows, cols = rows[::step], cols[::step]
    z = depth[rows, cols]
    return torch.stack(((cols - CENTRE_X) * z / FOCAL, (rows - CENTRE_Y) * z / FOCAL, z), dim=1)


class VoxelMerger:
    """Merges points into voxels in batches, so memory follows surface size, not frame count."""

    def __init__(self, device):
        self.device = device
        self.keys = torch.empty(0, dtype=torch.int64, device=device)
        self.sums = torch.empty(0, 6, dtype=torch.float64, device=device)
        self.counts = torch.empty(0, dtype=torch.int64, device=device)
        self.pending = []
        self.pending_points = 0

    def add(self, points, colours):
        self.pending.append(torch.cat((points, colours), dim=1).cpu())
        self.pending_points += points.shape[0]
        if self.pending_points >= MERGE_BATCH_POINTS:
            self.merge()

    def merge(self):
        if not self.pending:
            return
        batch = torch.cat(self.pending).to(self.device)
        self.pending, self.pending_points = [], 0
        k = (batch[:, :3] / CLOUD_VOXEL_MM).floor().long() + 200000
        keys = k[:, 0] * 400000**2 + k[:, 1] * 400000 + k[:, 2]
        merged_keys, inverse = torch.unique(torch.cat((self.keys, keys)), return_inverse=True)
        sums = torch.zeros(merged_keys.numel(), 6, dtype=torch.float64, device=self.device)
        counts = torch.zeros(merged_keys.numel(), dtype=torch.int64, device=self.device)
        old = self.keys.numel()
        sums.index_add_(0, inverse[:old], self.sums)
        sums.index_add_(0, inverse[old:], batch.double())
        counts.index_add_(0, inverse[:old], self.counts)
        counts.index_add_(0, inverse[old:], torch.ones(keys.numel(), dtype=torch.int64, device=self.device))
        self.keys, self.sums, self.counts = merged_keys, sums, counts

    def result(self):
        self.merge()
        keep = self.counts >= MIN_VOTES
        mean = (self.sums[keep] / self.counts[keep][:, None]).float()
        return mean[:, :3].cpu().numpy(), mean[:, 3:].clamp(0, 255).byte().cpu().numpy()


def build_cloud(depth_frames, colour, depth_ms, poses, shift, stride, device):
    merger = VoxelMerger(device)
    chosen = sorted(poses)[:: stride]
    for number, i in enumerate(chosen):
        camera = camera_points(depth_frames.read_clean(i), CLOUD_PIXEL_STEP)
        picture = decode_colour(colour, colour.nearest(depth_ms[i] - shift), device)
        if picture is None:
            continue
        rgb, seen = frame_colours(camera, picture)
        pose = torch.from_numpy(poses[i]).to(device).float()
        merger.add(camera[seen] @ pose[:3, :3].T + pose[:3, 3], rgb)
        if number % 10 == 0:
            say("Building point cloud", number / len(chosen))
    points, colours = merger.result()
    del merger
    torch.cuda.empty_cache()
    return points, colours


def surface_bricks(points, voxel, device):
    """Which fixed-size bricks of voxels lie near the surface, as integer coordinates."""
    size = voxel * BRICK_VOXELS
    cells = torch.unique((torch.from_numpy(points).to(device) / size).floor().long(), dim=0)
    steps = torch.tensor([-1, 0, 1], device=device)
    around = torch.cartesian_prod(steps, steps, steps)
    return torch.unique((cells[:, None, :] + around[None]).reshape(-1, 3), dim=0)


def choose_voxel(points, settings, device):
    """The finest voxel size whose surface bricks fit the memory limit."""
    limit = settings.vram_limit_gb if settings.use_gpu else settings.ram_limit_gb
    budget = limit * 1024**3 * VOLUME_SHARE
    voxel = settings.voxel_mm if settings.voxel_mm > 0 else MIN_VOXEL_MM
    voxel = max(voxel, MIN_VOXEL_MM)
    sample = points[:: max(len(points) // 2_000_000, 1)]
    while True:
        bricks = surface_bricks(sample, voxel, device)
        needed = bricks.shape[0] * BRICK_VOXELS**3 * BYTES_PER_VOXEL
        if needed <= budget:
            return voxel
        if settings.voxel_mm > 0:
            raise RuntimeError(
                f"{voxel} mm detail needs {needed / 1024**3:.1f} GB. Raise the memory limit or use auto detail."
            )
        voxel *= 1.12


class SparseVolume:
    """A signed distance field stored only in bricks near surfaces."""

    def __init__(self, points, voxel, device):
        self.voxel = voxel
        self.device = device
        bricks = surface_bricks(points, voxel, device)
        self.origin = bricks.min(dim=0).values
        self.dims = bricks.max(dim=0).values - self.origin + 1
        self.bricks = bricks
        self.table = torch.full((int(self.dims.prod()),), -1, dtype=torch.int32, device=device)
        self.table[self.brick_index(bricks)] = torch.arange(bricks.shape[0], dtype=torch.int32, device=device)
        count = bricks.shape[0] * BRICK_VOXELS**3
        self.sdf = torch.zeros(count, device=device)
        self.weight = torch.zeros(count, device=device)
        self.colour = torch.zeros(count, 3, device=device)
        self.colour_weight = torch.zeros(count, device=device)

    def brick_index(self, bricks):
        b = bricks - self.origin
        return (b[:, 0] * self.dims[1] + b[:, 1]) * self.dims[2] + b[:, 2]

    def locate(self, cells):
        """Storage index for voxel coordinates, or -1 for voxels with no brick."""
        bricks = torch.div(cells, BRICK_VOXELS, rounding_mode="floor")
        inside = ((bricks >= self.origin) & (bricks < self.origin + self.dims)).all(dim=1)
        slots = torch.full((cells.shape[0],), -1, dtype=torch.int64, device=self.device)
        slots[inside] = self.table[self.brick_index(bricks[inside])].long()
        local = cells - bricks * BRICK_VOXELS
        offset = (local[:, 0] * BRICK_VOXELS + local[:, 1]) * BRICK_VOXELS + local[:, 2]
        return torch.where(slots >= 0, slots * BRICK_VOXELS**3 + offset, torch.tensor(-1, device=self.device))


def image_normals(depth):
    """Camera-facing amount of each pixel's surface, from 0 (edge on) to 1 (facing)."""
    z = depth.nan_to_num(nan=0.0)
    dx = (z[1:-1, 2:] - z[1:-1, :-2]) * FOCAL / (2 * z[1:-1, 1:-1].clamp(min=1.0))
    dy = (z[2:, 1:-1] - z[:-2, 1:-1]) * FOCAL / (2 * z[1:-1, 1:-1].clamp(min=1.0))
    facing = torch.zeros_like(z)
    facing[1:-1, 1:-1] = 1 / torch.sqrt(1 + dx**2 + dy**2)
    return facing


def sharpness(picture):
    """Variance of a fast Laplacian: blurred frames score low."""
    gray = picture.float().mean(dim=2)[::2, ::2]
    laplacian = gray[1:-1, 1:-1] * 4 - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    return float(laplacian.var())


def fuse(depth_frames, colour, depth_ms, poses, shift, stride, cloud, settings, device):
    voxel = choose_voxel(cloud, settings, device)
    volume = SparseVolume(cloud[:: max(len(cloud) // 5_000_000, 1)], voxel, device)
    say("Fusing surfaces", 0.0, voxel_mm=round(voxel, 2), bricks=volume.bricks.shape[0])

    truncation = max(TRUNCATION_MM, voxel * MIN_TRUNCATION_VOXELS)
    steps = int(np.ceil(truncation / voxel))
    offsets = torch.arange(-steps, steps + 1, device=device).float() * voxel
    near_surface = offsets.abs() <= voxel * COLOUR_BAND_VOXELS
    chosen = sorted(poses)[:: stride]

    scores = []
    for number, i in enumerate(chosen):
        depth = depth_frames.read_clean(i)
        picture = decode_colour(colour, colour.nearest(depth_ms[i] - shift), device)
        if picture is None:
            continue
        score = sharpness(picture)
        scores.append(score)
        focus = min((score / float(np.median(scores[-200:]))) ** 2, 1.0)

        rows, cols = (~depth.isnan()).nonzero(as_tuple=True)
        z = depth[rows, cols]
        camera = torch.stack(((cols - CENTRE_X) * z / FOCAL, (rows - CENTRE_Y) * z / FOCAL, z), dim=1)
        rgb, seen = frame_colours(camera, picture)
        facing = image_normals(depth)[rows[seen], cols[seen]]
        camera = camera[seen]
        pose = torch.from_numpy(poses[i]).to(device).float()

        scale = 1 + offsets[None, :] / camera[:, 2:3]  # steps along each pixel's ray
        world = (camera[:, None, :] * scale[:, :, None]).reshape(-1, 3) @ pose[:3, :3].T + pose[:3, 3]
        index = volume.locate((world / voxel).floor().long())
        distance = (-offsets / truncation).repeat(camera.shape[0])
        strength = facing.repeat_interleave(offsets.numel())
        keep = index >= 0
        volume.sdf.index_add_(0, index[keep], distance[keep] * strength[keep])
        volume.weight.index_add_(0, index[keep], strength[keep])

        band = near_surface.repeat(camera.shape[0]) & keep & (strength > MIN_FACING)
        band_index = index[band]
        band_rgb = rgb.repeat_interleave(offsets.numel(), dim=0)[band]
        band_weight = (strength[band] ** 2 * focus).clamp(min=1e-3)
        known = volume.colour_weight[band_index] > 0
        if int(known.sum()) > 1000:
            sample = known.nonzero().squeeze(1)[:: max(int(known.sum()) // GAIN_SAMPLES, 1)]
            stored = volume.colour[band_index[sample]] / volume.colour_weight[band_index[sample]][:, None]
            gain = (stored.sum(dim=1) / band_rgb[sample].sum(dim=1).clamp(min=1.0)).median().clamp(*GAIN_LIMITS)
            band_rgb = (band_rgb * gain).clamp(max=255.0)
        volume.colour.index_add_(0, band_index, band_rgb * band_weight[:, None])
        volume.colour_weight.index_add_(0, band_index, band_weight)
        if number % 10 == 0:
            say("Fusing surfaces", number / len(chosen))
    return volume


def extract_chunk(volume, chunk):
    """Runs marching cubes on one block of bricks. Returns voxel-space vertices, faces, colour."""
    size = CHUNK_BRICKS * BRICK_VOXELS + 1
    axis = torch.arange(size, device=volume.device)
    grid = torch.cartesian_prod(axis, axis, axis) + chunk * CHUNK_BRICKS * BRICK_VOXELS
    index = volume.locate(grid)
    found = index >= 0
    weight = torch.zeros(grid.shape[0], device=volume.device)
    field = torch.ones(grid.shape[0], device=volume.device)
    weight[found] = volume.weight[index[found]]
    measured = weight >= MIN_WEIGHT
    field[measured] = volume.sdf[index[measured]] / weight[measured]
    if not bool(measured.any()):
        return None
    shape = (size, size, size)
    field_cpu = field.reshape(shape).cpu().numpy()
    if field_cpu.min() >= 0 or field_cpu.max() <= 0:
        return None
    mask = measured.reshape(shape).cpu().numpy()
    try:
        vertices, faces, _, _ = measure.marching_cubes(field_cpu, 0.0, mask=mask, allow_degenerate=False)
    except (ValueError, RuntimeError):
        return None
    if len(faces) == 0:
        return None

    cells = torch.from_numpy(np.round(vertices).astype(np.int64)).to(volume.device)
    colour_index = index[(cells[:, 0] * size + cells[:, 1]) * size + cells[:, 2]]
    rgb = torch.full((cells.shape[0], 3), 128.0, device=volume.device)
    good = (colour_index >= 0)
    good_index = colour_index[good]
    weights = volume.colour_weight[good_index].clamp(min=1e-6)[:, None]
    rgb[good] = volume.colour[good_index] / weights
    origin = (chunk * CHUNK_BRICKS * BRICK_VOXELS).cpu().numpy()
    return vertices.astype(np.float64) + origin, faces, rgb.clamp(0, 255).byte().cpu().numpy()


def weld(vertices, colours, faces):
    """Joins vertices that match along chunk seams."""
    keys = np.round(vertices * 64).astype(np.int64)
    keys -= keys.min(axis=0)
    span = keys.max(axis=0) + 1
    packed = (keys[:, 0] * span[1] + keys[:, 1]) * span[2] + keys[:, 2]
    _, first, inverse = np.unique(packed, return_index=True, return_inverse=True)
    faces = inverse[faces].astype(np.int32)
    good = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    return vertices[first], colours[first], faces[good]


def vertex_normals(vertices, faces):
    corners = vertices[faces]
    face_normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = np.zeros_like(vertices)
    for k in range(3):
        for axis in range(3):
            normals[:, axis] += np.bincount(faces[:, k], weights=face_normals[:, axis], minlength=len(vertices))
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(length, 1e-12)


def extract_mesh(volume):
    chunks = torch.unique(torch.div(volume.bricks, CHUNK_BRICKS, rounding_mode="floor"), dim=0)
    parts_v, parts_f, parts_c = [], [], []
    start = 0
    for number, chunk in enumerate(chunks):
        result = extract_chunk(volume, chunk)
        if result is not None:
            vertices, faces, colours = result
            parts_v.append(vertices)
            parts_c.append(colours)
            parts_f.append(faces + start)
            start += len(vertices)
        if number % 5 == 0:
            say("Extracting mesh", number / chunks.shape[0])
    vertices, colours, faces = weld(np.concatenate(parts_v), np.concatenate(parts_c), np.concatenate(parts_f))
    world = (vertices * volume.voxel).astype(np.float32)
    # Marching cubes winds these faces so that normals point away from the scanner's side.
    return world, (-vertex_normals(vertices, faces)).astype(np.float32), colours, faces


def remove_small_pieces(vertices, normals, rgb, faces):
    """Drops loose fragments, which are nearly always noise."""
    a, b, c = faces.T
    links = coo_matrix(
        (np.ones(len(faces) * 2, dtype=bool), (np.concatenate((a, b)), np.concatenate((b, c)))),
        shape=(len(vertices), len(vertices)),
    )
    _, labels = connected_components(links, directed=False)
    sizes = np.bincount(labels[a])
    keep_face = sizes[labels[a]] >= MIN_PIECE_FACES
    faces = faces[keep_face]
    used = np.zeros(len(vertices), dtype=bool)
    used[faces.ravel()] = True
    remap = np.cumsum(used) - 1
    return vertices[used], normals[used], rgb[used], remap[faces].astype(np.int32)


def build(folder, settings):
    started = time.time()
    device = device_for(settings)
    torch.set_num_threads(settings.cpu_threads)
    session = Session(folder)
    offset = MODE_OFFSETS[session.mode]
    depth_ms, _ = read_index(session.depth_index)
    count = len(depth_ms)
    depth_frames = DepthFrames(session, offset, device)
    colour = ColourFrames(session)

    say("Aligning frames", 0.0)
    poses, stats = align(depth_frames, colour, depth_ms, count, device)
    with open(session.poses_path, "w") as f:
        for i in sorted(poses):
            f.write(f"{i}," + ",".join(f"{x:.9g}" for x in poses[i].ravel()) + "\n")

    say("Matching colour timing", None)
    shift = find_time_shift(depth_frames, depth_ms, colour, poses)

    stride = max(settings.build_frame_stride, 1)
    cloud, cloud_rgb = build_cloud(depth_frames, colour, depth_ms, poses, shift, stride, device)
    write_cloud(session.cloud_path, camera_to_view(cloud), cloud_rgb)
    say("Point cloud ready", 1.0, cloud=str(session.cloud_path), points=len(cloud))

    volume = fuse(depth_frames, colour, depth_ms, poses, shift, stride, cloud, settings, device)
    voxel = volume.voxel
    vertices, normals, rgb, faces = extract_mesh(volume)
    say("Cleaning mesh", None)
    vertices, normals, rgb, faces = remove_small_pieces(vertices, normals, rgb, faces)
    vertices, normals = camera_to_view(vertices), camera_to_view(normals)
    write_mesh(session.mesh_path, vertices, normals, rgb, faces)
    write_stl(session.stl_path, vertices, faces)

    summary = {
        "frames": count, "aligned": len(poses), "points": len(cloud), "vertices": len(vertices),
        "triangles": len(faces), "voxel_mm": round(voxel, 2), "colour_shift_ms": shift,
        "seconds": round(time.time() - started, 1),
        "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 2) if device.type == "cuda" else 0,
        **stats,
    }
    session.folder.joinpath("build.json").write_text(json.dumps(summary, indent=2))
    say("Done", 1.0, mesh=str(session.mesh_path), cloud=str(session.cloud_path), summary=summary)


def main(arguments):
    parser = argparse.ArgumentParser(prog="ubuntu-ferret-scanner build")
    parser.add_argument("session")
    args = parser.parse_args(arguments)
    build(args.session, Settings.load())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
