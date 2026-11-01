"""Turns live frames into previews, a depth reading, tracking and the rough live cloud."""
import time
from dataclasses import dataclass, field

import numpy as np
import torch

from ubuntu_ferret_scanner.calibration import (
    CENTRE_X,
    CENTRE_Y,
    COLOUR_SIZE,
    FOCAL,
    HEIGHT,
    MODE_OFFSETS,
    WIDTH,
    decode_y14,
    depth_limits,
    project_to_colour,
    to_depth_mm,
)
from ubuntu_ferret_scanner.ply import camera_to_view
from ubuntu_ferret_scanner.relocalise import COLOUR_DRAFT, Relocaliser, decode_jpeg, describe
from ubuntu_ferret_scanner.tracking import Tracker, relative_angle

PREVIEW_STEP = 2
CLOUD_PIXEL_STEP = 4
CLOUD_FRAME_STEP = 3
GAUGE_WINDOW = 200  # pixels either side of the frame centre
MIN_GAUGE_FILL = 0.05
RELOCALISE_EVERY = 3  # frames between attempts while lost
AGREE_ATTEMPTS = 4  # a first match must be confirmed within this many attempts
AGREE_MM = 60.0
AGREE_RAD = 0.35


@dataclass
class Update:
    depth_preview: np.ndarray | None = None
    distance_mm: float | None = None
    fill: float = 0.0
    state: str = "idle"
    frames: int = 0
    lost_frames: int = 0
    recoveries: int = 0
    points: np.ndarray | None = None
    colours: np.ndarray | None = None
    pose: np.ndarray | None = None
    total_points: int = 0
    process_ms: float = 0.0
    extra: dict = field(default_factory=dict)


def turbo_table():
    """A rainbow depth colour table: red is near, blue is far."""
    x = np.linspace(0, 1, 256)
    r = np.clip(1.5 - np.abs(4 * x - 1), 0, 1)
    g = np.clip(1.5 - np.abs(4 * x - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * x - 3), 0, 1)
    return (np.column_stack((r, g, b)) * 255).astype(np.uint8)


class LivePipeline:
    def __init__(self, device, settings, mode):
        self.device = device
        self.settings = settings
        self.set_mode(mode)
        self.table = torch.from_numpy(turbo_table()).to(device)
        v, u = torch.meshgrid(
            torch.arange(0, HEIGHT, CLOUD_PIXEL_STEP, device=device),
            torch.arange(0, WIDTH, CLOUD_PIXEL_STEP, device=device),
            indexing="ij",
        )
        self.grid_rows, self.grid_cols = v.ravel(), u.ravel()
        self.colour = None
        self.poses_file = None
        self.reset_scan()

    def set_mode(self, mode):
        self.mode = mode
        self.offset = MODE_OFFSETS[mode]
        self.near_mm, self.far_mm = depth_limits(mode)

    def reset_scan(self):
        self.scanning = False
        self.tracker = None
        self.frames = 0
        self.lost_frames = 0
        self.seen = torch.empty(0, dtype=torch.int64, device=self.device)
        self.total_points = 0
        self.relocaliser = None
        self.key_poses = {}
        self.lost_run = 0
        self.recoveries = 0
        self.pending = None
        self.pending_age = 0

    def start_scan(self, session):
        self.reset_scan()
        self.tracker = Tracker(self.device)
        self.relocaliser = Relocaliser(self.device)
        self.poses_file = open(session.poses_path, "w")
        self.scanning = True

    def stop_scan(self):
        self.scanning = False
        if self.poses_file:
            self.poses_file.close()
            self.poses_file = None

    def add_colour(self, frame):
        picture = decode_jpeg(frame.data)
        if picture is not None:
            self.colour = picture
        return picture

    def depth_preview(self, depth):
        small = depth[:: PREVIEW_STEP, :: PREVIEW_STEP]
        scaled = ((small - self.near_mm) / (self.far_mm - self.near_mm)).nan_to_num(nan=-1.0)
        index = (scaled.clamp(0, 1) * 255).long()
        picture = self.table[index]
        picture[scaled < 0] = 0
        return picture.cpu().numpy()

    def gauge(self, depth):
        cy, cx = HEIGHT // 2, WIDTH // 2
        patch = depth[cy - GAUGE_WINDOW : cy + GAUGE_WINDOW, cx - GAUGE_WINDOW : cx + GAUGE_WINDOW]
        good = patch[~patch.isnan()]
        if good.numel() < patch.numel() * MIN_GAUGE_FILL:
            return None
        return float(good.median())

    def add_points(self, depth, pose):
        rows, cols = self.grid_rows, self.grid_cols
        z = depth[rows, cols]
        keep = ~z.isnan()
        rows, cols, z = rows[keep], cols[keep], z[keep]
        camera = torch.stack(((cols - CENTRE_X) * z / FOCAL, (rows - CENTRE_Y) * z / FOCAL, z), dim=1)
        world = camera.double() @ pose[:3, :3].T + pose[:3, 3]

        voxel = self.settings.live_voxel_mm
        k = (world / voxel).floor().long() + 200000
        keys = k[:, 0] * 400000**2 + k[:, 1] * 400000 + k[:, 2]
        keys, first = unique_first(keys)
        fresh = ~torch.isin(keys, self.seen)
        room = self.settings.live_max_points - self.total_points
        chosen = first[fresh][: max(room, 0)]
        if chosen.numel() == 0:
            return None, None
        self.seen = torch.cat((self.seen, keys[fresh][: max(room, 0)]))
        self.total_points += chosen.numel()

        colours = np.full((chosen.numel(), 3), 170, dtype=np.uint8)
        if self.colour is not None:
            u, v = project_to_colour(camera[chosen])
            h, w = self.colour.shape[:2]
            cu = (u * w / COLOUR_SIZE[0]).long().clamp(0, w - 1).cpu().numpy()
            cv = (v * h / COLOUR_SIZE[1]).long().clamp(0, h - 1).cpu().numpy()
            colours = self.colour[cv, cu]
        return camera_to_view(world[chosen].float().cpu().numpy()), colours

    def remember_keyframe(self, depth, levels, pose):
        """Saves a keyframe that tracking can return to if it gets lost."""
        if self.colour is None:
            return
        view = describe(depth, self.colour)
        if view is not None:
            key = len(self.key_poses)
            self.key_poses[key] = pose.clone()
            self.relocaliser.add(key, view, levels)

    def try_relocalise(self, depth, levels):
        """Looks for the current view in the scan. A position must be found twice to count."""
        if self.colour is None:
            return False
        match = self.relocaliser.find(describe(depth, self.colour), levels)
        self.pending_age += 1
        if self.pending is not None and self.pending_age > AGREE_ATTEMPTS:
            self.pending = None
        if match is None:
            return False

        pose = self.key_poses[match.key] @ match.T
        if self.pending is not None:
            change = torch.linalg.inv(self.pending) @ pose
            if float(change[:3, 3].norm()) < AGREE_MM and relative_angle(change) < AGREE_RAD:
                self.tracker.rejoin(pose, levels)
                self.pending = None
                return True
        self.pending = pose
        self.pending_age = 0
        return False

    def process_depth(self, frame, want_preview):
        started = time.perf_counter()
        raw = torch.frombuffer(bytearray(frame.data), dtype=torch.uint8).to(self.device)
        depth = to_depth_mm(decode_y14(raw), self.offset)
        update = Update(distance_mm=self.gauge(depth))
        update.fill = float((~depth.isnan()).float().mean())
        if want_preview:
            update.depth_preview = self.depth_preview(depth)

        if self.scanning:
            number = self.frames
            self.frames += 1
            result, levels = self.tracker.feed(depth)
            update.state = result.state
            if result.new_keyframe:
                self.remember_keyframe(depth, levels, result.pose)
            if result.pose is None:
                self.lost_frames += 1
                self.lost_run += 1
                if self.lost_run % RELOCALISE_EVERY == 0 and self.try_relocalise(depth, levels):
                    update.state = "recovered"
                    self.recoveries += 1
                    self.lost_run = 0
            else:
                self.lost_run = 0
                pose = result.pose.cpu().numpy()
                update.pose = pose
                self.poses_file.write(f"{number}," + ",".join(f"{x:.9g}" for x in pose.ravel()) + "\n")
                if number % CLOUD_FRAME_STEP == 0:
                    update.points, update.colours = self.add_points(depth, result.pose)
        update.frames = self.frames
        update.lost_frames = self.lost_frames
        update.recoveries = self.recoveries
        update.total_points = self.total_points
        update.process_ms = (time.perf_counter() - started) * 1000
        return update


def unique_first(keys):
    """Unique keys, and the index of one occurrence of each."""
    unique, inverse = torch.unique(keys, return_inverse=True)
    first = torch.zeros(unique.numel(), dtype=torch.long, device=keys.device)
    first[inverse] = torch.arange(keys.numel(), device=keys.device)
    return unique, first
