"""Frame-to-keyframe tracking on the GPU.

Point-to-plane ICP (Chen and Medioni) with projective matching (Newcombe et al., KinectFusion).
"""
from dataclasses import dataclass

import torch

from ubuntu_ferret_scanner.calibration import CENTRE_X, CENTRE_Y, FOCAL

DOWNSCALES = (8, 4)
ITERATIONS = 12
MATCH_MM = 12.0
MIN_NORMAL_COS = 0.7
KEYFRAME_MM = 25.0
KEYFRAME_RAD = 0.09
LOST_INLIERS = 0.25
MIN_MATCHES = 200


def downscale(depth, factor):
    """Block median, ignoring pixels without depth."""
    h, w = depth.shape[0] // factor, depth.shape[1] // factor
    blocks = depth[: h * factor, : w * factor].reshape(h, factor, w, factor)
    blocks = blocks.permute(0, 2, 1, 3).reshape(h, w, -1)
    ordered = blocks.sort(dim=2).values  # NaN sorts last
    count = (~blocks.isnan()).sum(dim=2)
    middle = ordered.gather(2, (count // 2).clamp(max=factor * factor - 1).unsqueeze(2)).squeeze(2)
    return torch.where(count >= factor * factor // 2, middle, float("nan"))


class Level:
    """Vertex and normal maps for one image scale."""

    def __init__(self, depth, factor):
        self.h, self.w = depth.shape
        self.focal = FOCAL / factor
        self.cx, self.cy = CENTRE_X / factor, CENTRE_Y / factor
        v, u = torch.meshgrid(
            torch.arange(self.h, device=depth.device, dtype=torch.float32),
            torch.arange(self.w, device=depth.device, dtype=torch.float32),
            indexing="ij",
        )
        x = (u - self.cx) * depth / self.focal
        y = (v - self.cy) * depth / self.focal
        self.vertices = torch.stack((x, y, depth), dim=2)

        du = self.vertices[1:-1, 2:] - self.vertices[1:-1, :-2]
        dv = self.vertices[2:, 1:-1] - self.vertices[:-2, 1:-1]
        normals = torch.linalg.cross(du, dv, dim=2)
        normals = -normals / normals.norm(dim=2, keepdim=True)
        self.normals = torch.full(self.vertices.shape, float("nan"), device=depth.device)
        self.normals[1:-1, 1:-1] = normals
        self.valid = ~self.normals.isnan().any(dim=2)


def build_levels(depth):
    return {factor: Level(downscale(depth, factor), factor) for factor in DOWNSCALES}


def small_transform(delta):
    wx, wy, wz, tx, ty, tz = delta.tolist()
    rotation = torch.tensor(
        [[1, -wz, wy], [wz, 1, -wx], [-wy, wx, 1]], dtype=torch.float64, device=delta.device
    )
    u, _, vt = torch.linalg.svd(rotation)
    T = torch.eye(4, dtype=torch.float64, device=delta.device)
    T[:3, :3] = u @ vt
    T[:3, 3] = delta[3:]
    return T


def icp_level(current, key, T, match_mm=MATCH_MM):
    """Returns the refined transform, inlier share, RMS error and position strength."""
    points = current.vertices[current.valid]
    normals_c = current.normals[current.valid]
    inliers, rms, condition = 0.0, float("inf"), 0.0
    for _ in range(ITERATIONS):
        R, t = T[:3, :3].float(), T[:3, 3].float()
        p = points @ R.T + t
        n_c = normals_c @ R.T
        u = (p[:, 0] * key.focal / p[:, 2] + key.cx).round().long()
        v = (p[:, 1] * key.focal / p[:, 2] + key.cy).round().long()
        inside = (u >= 0) & (u < key.w) & (v >= 0) & (v < key.h) & (p[:, 2] > 0)
        index = inside.nonzero().squeeze(1)
        index = index[key.valid[v[index], u[index]]]
        if len(index) < MIN_MATCHES:
            return T, 0.0, float("inf"), 0.0

        q = key.vertices[v[index], u[index]]
        n = key.normals[v[index], u[index]]
        pi = p[index]
        keep = ((pi - q).norm(dim=1) < match_mm) & ((n * n_c[index]).sum(dim=1) > MIN_NORMAL_COS)
        if int(keep.sum()) < MIN_MATCHES:
            return T, 0.0, float("inf"), 0.0

        pi, q, n = pi[keep].double(), q[keep].double(), n[keep].double()
        residual = ((pi - q) * n).sum(dim=1)
        scale = torch.clamp(residual.abs().median() * 1.5, min=0.3)
        weight = (scale / residual.abs().clamp(min=1e-6)).clamp(max=1.0)
        J = torch.cat((torch.linalg.cross(pi, n, dim=1), n), dim=1)
        A = (J * weight[:, None]).T @ J
        b = (J * weight[:, None]).T @ -residual
        delta = torch.linalg.solve(A + torch.eye(6, dtype=A.dtype, device=A.device) * 1e-3, b)
        T = small_transform(delta) @ T
        inliers = int(keep.sum()) / max(len(points), 1)
        rms = float(residual.square().mean().sqrt())
        strength = torch.linalg.eigvalsh(A[3:, 3:])
        condition = float(strength[0] / strength[-1])
        if float(delta[:3].abs().max()) < 1e-5 and float(delta[3:].abs().max()) < 1e-3:
            break
    return T, inliers, rms, condition


def relative_angle(T):
    return float(torch.arccos(((torch.trace(T[:3, :3]) - 1) / 2).clamp(-1, 1)))


@dataclass
class TrackResult:
    state: str  # "first", "ok" or "lost"
    pose: torch.Tensor | None = None
    inliers: float = 0.0
    rms: float = float("inf")
    new_keyframe: bool = False


class Tracker:
    def __init__(self, device):
        self.device = device
        self.key_levels = None
        self.key_pose = torch.eye(4, dtype=torch.float64, device=device)
        self.T_key = torch.eye(4, dtype=torch.float64, device=device)
        self.step = torch.eye(4, dtype=torch.float64, device=device)

    def feed(self, depth):
        levels = build_levels(depth)
        if self.key_levels is None:
            self.key_levels = levels
            return TrackResult("first", self.key_pose.clone(), new_keyframe=True), levels

        T, inliers, rms = self.step @ self.T_key, 0.0, float("inf")
        for factor in DOWNSCALES:
            T, inliers, rms, _ = icp_level(levels[factor], self.key_levels[factor], T)
            if inliers == 0.0:
                break
        if inliers < LOST_INLIERS:
            self.step = torch.eye(4, dtype=torch.float64, device=self.device)
            return TrackResult("lost", None, inliers), levels

        self.step = T @ torch.linalg.inv(self.T_key)
        self.T_key = T
        pose = self.key_pose @ T
        result = TrackResult("ok", pose, inliers, rms)
        if float(T[:3, 3].norm()) > KEYFRAME_MM or relative_angle(T) > KEYFRAME_RAD:
            self.key_pose = pose
            self.key_levels = levels
            self.T_key = torch.eye(4, dtype=torch.float64, device=self.device)
            result.new_keyframe = True
        return result, levels
