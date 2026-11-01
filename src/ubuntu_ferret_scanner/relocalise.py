"""Finds the scanner's position in an existing scan when tracking has been lost.

ORB features (Rublee et al.) in the colour image are lifted to 3D with the depth image, matched
against saved keyframes, and solved by RANSAC with the Kabsch method. ICP on depth then has the
final say, so colour alone can never place the scanner.
"""
import io
from dataclasses import dataclass

import cv2
import numpy as np
import torch
from PIL import Image

from ubuntu_ferret_scanner.calibration import (
    CENTRE_X,
    CENTRE_Y,
    COLOUR_SIZE,
    FOCAL,
    project_to_colour,
    remove_edges,
)
from ubuntu_ferret_scanner.tracking import DOWNSCALES, icp_level

FEATURES = 2000
COLOUR_DRAFT = (960, 540)
LOOKUP_SCALE = 4  # colour pixels per cell when matching features to depth
DEPTH_STEP = 2
MIN_FEATURES = 40

DESCRIPTOR_BYTES = 32
VOTE_DISTANCE = 52
MATCH_DISTANCE = 64
RATIO = 0.9
CANDIDATES = 16
CHUNK = 20000

RANSAC_ROUNDS = 1000
RANSAC_MM = 7.0
MIN_RANSAC_INLIERS = 12

VERIFY_SEARCH_MM = 30.0
VERIFY_INLIERS = 0.35
VERIFY_RMS_MM = 1.6
VERIFY_CONDITION = 0.02
MAX_ICP_SHIFT_MM = 40.0


def decode_jpeg(data):
    """Decodes a colour frame at half size. Returns None if the frame is damaged."""
    try:
        image = Image.open(io.BytesIO(data))
        image.draft("RGB", COLOUR_DRAFT)
        return np.asarray(image.convert("RGB"))
    except OSError:
        return None


@dataclass
class View:
    """Features of one frame: 3D points in its depth camera, and their descriptors."""

    points: np.ndarray
    descriptors: np.ndarray


def describe(depth, picture):
    """Builds a View from a depth tensor and an RGB colour image. Returns None if too sparse."""
    gray = cv2.cvtColor(picture, cv2.COLOR_RGB2GRAY)
    detector = cv2.ORB_create(nfeatures=FEATURES, fastThreshold=12)
    keypoints, descriptors = detector.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < MIN_FEATURES:
        return None

    clean = remove_edges(depth)
    rows, cols = (~clean.isnan()).nonzero(as_tuple=True)
    rows, cols = rows[:: DEPTH_STEP], cols[:: DEPTH_STEP]
    z = clean[rows, cols]
    camera = torch.stack(((cols - CENTRE_X) * z / FOCAL, (rows - CENTRE_Y) * z / FOCAL, z), dim=1)
    u, v = project_to_colour(camera)
    cu, cv_ = (u / LOOKUP_SCALE).long(), (v / LOOKUP_SCALE).long()
    width, height = COLOUR_SIZE[0] // LOOKUP_SCALE, COLOUR_SIZE[1] // LOOKUP_SCALE
    inside = (cu >= 0) & (cu < width) & (cv_ >= 0) & (cv_ < height)
    order = inside.nonzero().squeeze(1)
    order = order[z[order].argsort(descending=True)]  # nearest written last
    lookup = torch.full((height, width), -1, dtype=torch.long, device=depth.device)
    lookup[cv_[order], cu[order]] = order
    # Corners often sit just past a removed depth edge, so borrow from the next cell.
    for shift, dim in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
        lookup = torch.where(lookup < 0, lookup.roll(shift, dim), lookup)

    scale = COLOUR_SIZE[0] / picture.shape[1] / LOOKUP_SCALE
    cells = np.array([k.pt for k in keypoints], dtype=np.float32) * scale
    cx = np.minimum(cells[:, 0].astype(int), width - 1)
    cy = np.minimum(cells[:, 1].astype(int), height - 1)
    found = lookup[torch.from_numpy(cy).to(depth.device), torch.from_numpy(cx).to(depth.device)]
    good = found >= 0
    if int(good.sum()) < MIN_FEATURES:
        return None
    return View(camera[found[good]].cpu().numpy(), descriptors[good.cpu().numpy()])


def solve_rigid(source, target, weights=None):
    """Batched Kabsch: the rotation and translation mapping source onto target."""
    if weights is None:
        weights = torch.ones(source.shape[:2], dtype=source.dtype, device=source.device)
    w = (weights / weights.sum(dim=1, keepdim=True).clamp(min=1e-9))[:, :, None]
    source_mean = (source * w).sum(dim=1, keepdim=True)
    target_mean = (target * w).sum(dim=1, keepdim=True)
    H = ((source - source_mean) * w).transpose(1, 2) @ (target - target_mean)
    u, _, vt = torch.linalg.svd(H)
    d = torch.sign(torch.linalg.det(vt.transpose(1, 2) @ u.transpose(1, 2)))
    fix = torch.eye(3, dtype=source.dtype, device=source.device).repeat(len(source), 1, 1)
    fix[:, 2, 2] = d
    R = vt.transpose(1, 2) @ fix @ u.transpose(1, 2)
    t = target_mean.squeeze(1) - (R @ source_mean.transpose(1, 2)).squeeze(2)
    return R, t


def ransac(source, target, device):
    """Returns a 4x4 transform from source to target, and its inlier count."""
    count = source.shape[0]
    if count < MIN_RANSAC_INLIERS:
        return None, 0
    generator = torch.Generator(device=device).manual_seed(count)
    picks = torch.rand(RANSAC_ROUNDS, count, device=device, generator=generator).argsort(dim=1)[:, :3]
    R, t = solve_rigid(source[picks], target[picks])
    moved = (source[None] @ R.transpose(1, 2)) + t[:, None, :]
    distance = (moved - target[None]).norm(dim=2)
    inliers = distance < RANSAC_MM
    scores = inliers.sum(dim=1)
    best = int(scores.argmax())
    keep = inliers[best]
    if int(keep.sum()) < MIN_RANSAC_INLIERS:
        return None, int(keep.sum())
    R, t = solve_rigid(source[keep][None], target[keep][None])
    T = torch.eye(4, dtype=torch.float64, device=device)
    T[:3, :3], T[:3, 3] = R[0].double(), t[0].double()
    return T, int(keep.sum())


def verify(levels, key_levels, guess, device):
    """Checks a position with ICP on depth. Returns the refined transform and quality."""
    finest = DOWNSCALES[-1]
    T = guess
    for factor in DOWNSCALES:
        T, inliers, _, _ = icp_level(levels[factor], key_levels[factor].to(device), T, VERIFY_SEARCH_MM)
        if inliers == 0.0:
            return None
    T, inliers, rms, condition = icp_level(levels[finest], key_levels[finest].to(device), T)
    shift = float((T[:3, 3] - guess[:3, 3]).norm())
    good = (
        inliers >= VERIFY_INLIERS
        and rms <= VERIFY_RMS_MM
        and condition >= VERIFY_CONDITION
        and shift <= MAX_ICP_SHIFT_MM
    )
    return (T, inliers, rms) if good else None


@dataclass
class Match:
    key: int
    T: torch.Tensor  # the query frame's pose in the keyframe's camera
    feature_inliers: int
    inliers: float
    rms: float


class Relocaliser:
    """Holds keyframe features and finds which keyframe a new view belongs to."""

    def __init__(self, device):
        self.device = device
        self.points = {}
        self.descriptors = {}
        self.levels = {}
        self.pool = None
        self.owner = None
        self.keys = []
        self.bits = torch.tensor(
            np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).astype(np.float32) * 2 - 1,
            device=device,
        )

    def add(self, key, view, levels):
        """Saves a keyframe. Its depth levels stay in system memory until needed."""
        self.points[key] = torch.from_numpy(view.points).to(self.device)
        self.descriptors[key] = torch.from_numpy(view.descriptors).to(self.device)
        self.levels[key] = {factor: level.to("cpu") for factor, level in levels.items()}
        self.pool = None

    def signs(self, descriptors):
        return self.bits[descriptors.long()].reshape(descriptors.shape[0], DESCRIPTOR_BYTES * 8)

    def build_pool(self):
        self.keys = sorted(self.descriptors)
        self.pool = torch.cat([self.descriptors[k] for k in self.keys])
        self.owner = torch.cat(
            [torch.full((len(self.descriptors[k]),), n, device=self.device) for n, k in enumerate(self.keys)]
        )

    def rank(self, query):
        """Keyframes sorted by how many query features look like theirs."""
        votes = torch.zeros(len(self.keys), device=self.device)
        limit = DESCRIPTOR_BYTES * 8 - 2 * VOTE_DISTANCE
        for start in range(0, len(self.pool), CHUNK):
            block = self.signs(self.pool[start : start + CHUNK])
            close = (query @ block.T) > limit
            votes.index_add_(0, self.owner[start : start + CHUNK], close.float().sum(dim=0))
        return votes

    def pair(self, query, query_points, key):
        """Features shared with one keyframe, as matching 3D point lists."""
        block = self.signs(self.descriptors[key])
        if len(block) < 2:
            return None, None
        distance = (DESCRIPTOR_BYTES * 8 - query @ block.T) / 2
        nearest = distance.topk(2, dim=1, largest=False)
        keep = (nearest.values[:, 0] <= MATCH_DISTANCE) & (nearest.values[:, 0] < RATIO * nearest.values[:, 1])
        return query_points[keep], self.points[key][nearest.indices[keep, 0]]

    def find(self, view, levels, allowed=None):
        """Looks for this view among the keyframes. Returns a verified Match or None."""
        if view is None or not self.descriptors:
            return None
        if self.pool is None:
            self.build_pool()
        query = self.signs(torch.from_numpy(view.descriptors).to(self.device))
        query_points = torch.from_numpy(view.points).to(self.device)
        votes = self.rank(query)
        if allowed is not None:
            mask = torch.tensor([k in allowed for k in self.keys], device=self.device)
            votes = votes * mask
        tried = []
        for index in votes.topk(min(CANDIDATES, len(self.keys))).indices.tolist():
            key = self.keys[index]
            if float(votes[index]) == 0:
                continue
            source, target = self.pair(query, query_points, key)
            if source is None:
                continue
            T, count = ransac(source, target, self.device)
            if T is not None:
                tried.append((count, key, T))

        for count, key, T in sorted(tried, key=lambda item: -item[0]):
            result = verify(levels, self.levels[key], T, self.device)
            if result is not None:
                refined, inliers, rms = result
                return Match(key, refined, count, inliers, rms)
        return None
