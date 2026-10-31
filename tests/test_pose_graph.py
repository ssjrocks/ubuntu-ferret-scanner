import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from pose_graph import edge_error, exp_se3, log_se3, optimize


def circle_poses(count, radius=300.0):
    poses = []
    for k in range(count):
        angle = 2 * np.pi * k / count
        T = exp_se3(np.array([0, 0, angle, 0, 0, 0]))
        T[:3, 3] = (radius * np.cos(angle), radius * np.sin(angle), 0)
        poses.append(T)
    return poses


def drifted(truth, rng):
    """Chains noisy steps, as frame-to-frame tracking does."""
    poses = [truth[0].copy()]
    steps = []
    for a, b in zip(truth, truth[1:]):
        step = np.linalg.inv(a) @ b
        noise = np.concatenate((rng.normal(0, 0.003, 3), rng.normal(0, 1.0, 3)))
        step = step @ exp_se3(noise)
        steps.append(step)
        poses.append(poses[-1] @ step)
    return poses, steps


class Lie(unittest.TestCase):
    def test_log_undoes_exp(self):
        xi = np.array([0.2, -0.4, 0.3, 10.0, -5.0, 2.0])
        self.assertTrue(np.allclose(log_se3(exp_se3(xi)), xi))

    def test_small_rotation(self):
        xi = np.array([1e-9, 0, 0, 1.0, 2.0, 3.0])
        self.assertTrue(np.allclose(log_se3(exp_se3(xi)), xi, atol=1e-6))


class Optimize(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1)
        self.truth = circle_poses(40)
        self.poses, self.steps = drifted(self.truth, self.rng)
        self.edges = [(k, k + 1, step, 1.0) for k, step in enumerate(self.steps)]

    def test_loop_edge_removes_most_drift(self):
        closing = np.linalg.inv(self.truth[0]) @ self.truth[-1]
        before = np.linalg.norm(self.poses[-1][:3, 3] - self.truth[-1][:3, 3])
        result = optimize(self.poses, self.edges + [(0, 39, closing, 100.0)])
        after = np.linalg.norm(result[-1][:3, 3] - self.truth[-1][:3, 3])
        self.assertGreater(before, 5.0)
        self.assertLess(after, before * 0.05)

    def test_first_pose_stays_fixed(self):
        closing = np.linalg.inv(self.truth[0]) @ self.truth[-1]
        result = optimize(self.poses, self.edges + [(0, 39, closing, 100.0)])
        self.assertTrue(np.allclose(result[0], self.poses[0], atol=1e-5))

    def test_no_loop_changes_nothing(self):
        result = optimize(self.poses, self.edges)
        self.assertLess(np.abs(result[-1] - self.poses[-1]).max(), 1e-3)

    def test_consistent_edges_have_no_error(self):
        for k, step in enumerate(self.steps):
            error = edge_error(self.poses[k], self.poses[k + 1], step)
            self.assertLess(np.abs(error).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()
