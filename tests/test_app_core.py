import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ubuntu_ferret_scanner import calibration, ply
from ubuntu_ferret_scanner.reconstruct import choose_voxel
from ubuntu_ferret_scanner.settings import RESERVED_THREADS, Settings


class Decoding(unittest.TestCase):
    def test_y14_bit_layout(self):
        pixels = [0x2ABC, 0x1234, 0x3FFF, 0x0001]
        bits = "".join(f"{p:014b}" for p in pixels)
        group = [int(bits[i : i + 8], 2) for i in range(0, 56, 8)]
        raw = torch.tensor(group * (calibration.DEPTH_BYTES // 7), dtype=torch.uint8)
        values = calibration.decode_y14(raw)
        self.assertEqual(values[0, :4].tolist(), pixels)

    def test_zero_means_no_depth(self):
        depth = calibration.to_depth_mm(torch.tensor([[0, 4662]]), 100.0)
        self.assertTrue(depth[0, 0].isnan())
        self.assertAlmostEqual(float(depth[0, 1]), 415, delta=2)

    def test_mode_limits(self):
        near, far = calibration.depth_limits(calibration.LONG_MODE)
        self.assertAlmostEqual(near, 201, delta=2)
        self.assertAlmostEqual(far, 717, delta=2)


class Files(unittest.TestCase):
    def test_mesh_round_trip(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        normals = np.array([[0, 0, 1]] * 3, dtype=np.float32)
        colours = np.array([[255, 0, 0]] * 3, dtype=np.uint8)
        faces = np.array([[0, 1, 2]], dtype=np.int32)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mesh.ply"
            ply.write_mesh(path, vertices, normals, colours, faces)
            result = ply.read_mesh(path)
            ply.write_stl(Path(folder) / "mesh.stl", vertices, faces)
            self.assertEqual((Path(folder) / "mesh.stl").stat().st_size, 134)
        for got, wanted in zip(result, (vertices, normals, colours, faces)):
            self.assertTrue(np.array_equal(got, wanted))

    def test_view_axes_are_a_rotation(self):
        flip = np.diag([1.0, -1.0, -1.0])
        self.assertAlmostEqual(np.linalg.det(flip), 1.0)


class Resources(unittest.TestCase):
    def test_auto_configure_leaves_room(self):
        settings = Settings()
        settings.auto_configure()
        self.assertGreaterEqual(settings.cpu_threads, 1)
        self.assertLessEqual(settings.cpu_threads, max(__import__("os").cpu_count() - RESERVED_THREADS, 1))

    def test_voxel_grows_for_large_scans(self):
        settings = Settings(use_gpu=True, vram_limit_gb=8.0)
        small = choose_voxel((500.0, 500.0, 500.0), settings)
        large = choose_voxel((5000.0, 5000.0, 2000.0), settings)
        self.assertGreater(large, small)

    def test_voxel_respects_a_chosen_size(self):
        settings = Settings(use_gpu=True, vram_limit_gb=8.0, voxel_mm=3.0)
        self.assertEqual(choose_voxel((500.0, 500.0, 500.0), settings), 3.0)


if __name__ == "__main__":
    unittest.main()
