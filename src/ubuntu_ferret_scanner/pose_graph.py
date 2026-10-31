"""Pose graph optimisation on SE(3), using Gauss-Newton with the first pose held fixed.

Each edge is (i, j, measured, weight), where measured is the transform T_i^-1 T_j.
Follows the standard formulation in Grisetti et al., "A Tutorial on Graph-Based SLAM".
"""
import numpy as np

ANCHOR_WEIGHT = 1e8
EPSILON = 1e-9
# Rotation errors move a surface by their angle times its distance, so they are weighted by
# the square of a typical working distance in mm. Without this, the solver hides errors in
# rotation, which bends the model.
LEVER_MM = 400.0
INFORMATION = np.diag([LEVER_MM**2] * 3 + [1.0] * 3)


def hat(w):
    return np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])


def exp_se3(xi):
    """xi is (rotation vector, translation)."""
    w, v = xi[:3], xi[3:]
    theta = np.linalg.norm(w)
    W = hat(w)
    if theta < EPSILON:
        A, B, C = 1.0, 0.5, 1.0 / 6.0
    else:
        A = np.sin(theta) / theta
        B = (1 - np.cos(theta)) / theta**2
        C = (theta - np.sin(theta)) / theta**3
    T = np.eye(4)
    T[:3, :3] = np.eye(3) + A * W + B * W @ W
    T[:3, 3] = (np.eye(3) + B * W + C * W @ W) @ v
    return T


def log_se3(T):
    R, t = T[:3, :3], T[:3, 3]
    cos_theta = np.clip((np.trace(R) - 1) / 2, -1, 1)
    theta = np.arccos(cos_theta)
    if theta < 1e-6:
        w = 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
        return np.concatenate((w, t))

    W = (R - R.T) * theta / (2 * np.sin(theta))
    w = np.array([W[2, 1], W[0, 2], W[1, 0]])
    half = 0.5 * theta
    V_inverse = (
        np.eye(3) - 0.5 * W + (1 - half * np.cos(half) / np.sin(half)) / theta**2 * W @ W
    )
    return np.concatenate((w, V_inverse @ t))


def adjoint(T):
    R, t = T[:3, :3], T[:3, 3]
    A = np.zeros((6, 6))
    A[:3, :3] = R
    A[3:, 3:] = R
    A[3:, :3] = hat(t) @ R
    return A


def edge_error(T_i, T_j, measured):
    return log_se3(np.linalg.inv(measured) @ np.linalg.inv(T_i) @ T_j)


def optimize(poses, edges, iterations=12):
    """Returns corrected poses. The first pose does not move."""
    poses = [p.copy() for p in poses]
    size = 6 * len(poses)
    for _ in range(iterations):
        H = np.zeros((size, size))
        b = np.zeros(size)
        for i, j, measured, weight in edges:
            error = edge_error(poses[i], poses[j], measured)
            J_i = -adjoint(np.linalg.inv(poses[j]) @ poses[i])
            a, c = 6 * i, 6 * j
            omega = weight * INFORMATION
            H[a : a + 6, a : a + 6] += J_i.T @ omega @ J_i
            H[a : a + 6, c : c + 6] += J_i.T @ omega
            H[c : c + 6, a : a + 6] += omega @ J_i
            H[c : c + 6, c : c + 6] += omega
            b[a : a + 6] += J_i.T @ omega @ error
            b[c : c + 6] += omega @ error

        H[:6, :6] += np.eye(6) * ANCHOR_WEIGHT * LEVER_MM**2
        step = np.linalg.solve(H, -b)
        for k in range(len(poses)):
            poses[k] = poses[k] @ exp_se3(step[6 * k : 6 * k + 6])
        if np.abs(step).max() < 1e-7:
            break
    return poses
