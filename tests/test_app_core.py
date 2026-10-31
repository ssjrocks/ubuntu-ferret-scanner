import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ubuntu_ferret_scanner import calibration, ply
from ubuntu_ferret_scanner.reconstruct import (
    BRICK_VOXELS,
    EDGE_WINDOW,
    MIN_PIECE_FACES,
    SparseVolume,
    choose_voxel,
    extract_mesh,
    remove_edges,
    remove_small_pieces,
)
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


class Cleaning(unittest.TestCase):
    def test_small_pieces_go_and_large_ones_stay(self):
        count = MIN_PIECE_FACES + 5
        strip = np.array([[k, k + 1, k + 2] for k in range(count)], dtype=np.int32)
        loose = np.array([[0, 1, 2]], dtype=np.int32) + count + 2
        faces = np.concatenate((strip, loose))
        total = count + 5
        vertices = np.zeros((total, 3), dtype=np.float32)
        vertices[:, 0] = np.arange(total)
        normals = np.zeros((total, 3), dtype=np.float32)
        colours = np.zeros((total, 3), dtype=np.uint8)
        kept_vertices, _, _, kept_faces = remove_small_pieces(vertices, normals, colours, faces)
        self.assertEqual(len(kept_faces), count)
        self.assertEqual(len(kept_vertices), count + 2)
        self.assertEqual(int(kept_faces.max()), count + 1)


class Resources(unittest.TestCase):
    def test_auto_configure_leaves_room(self):
        settings = Settings()
        settings.auto_configure()
        self.assertGreaterEqual(settings.cpu_threads, 1)
        self.assertLessEqual(settings.cpu_threads, max(__import__("os").cpu_count() - RESERVED_THREADS, 1))

    def test_voxel_grows_when_memory_is_small(self):
        surface = np.random.default_rng(0).uniform(0, 900, (200000, 3)).astype(np.float32)
        surface[:, 2] = 0
        device = torch.device("cpu")
        roomy = choose_voxel(surface, Settings(use_gpu=True, vram_limit_gb=8.0), device)
        tight = choose_voxel(surface, Settings(use_gpu=True, vram_limit_gb=0.5), device)
        self.assertGreater(tight, roomy)

    def test_chosen_detail_is_kept_or_refused(self):
        surface = np.random.default_rng(0).uniform(0, 900, (200000, 3)).astype(np.float32)
        surface[:, 2] = 0
        device = torch.device("cpu")
        chosen = Settings(use_gpu=True, vram_limit_gb=8.0, voxel_mm=3.0)
        self.assertEqual(choose_voxel(surface, chosen, device), 3.0)
        with self.assertRaises(RuntimeError):
            choose_voxel(surface, Settings(use_gpu=True, vram_limit_gb=0.1, voxel_mm=0.5), device)


class Edges(unittest.TestCase):
    def test_pixels_beside_holes_and_jumps_are_removed(self):
        depth = torch.full((40, 60), 300.0)
        depth[:, 30:] = float("nan")
        depth[:, :10] = 500.0
        clean = remove_edges(depth)
        margin = EDGE_WINDOW // 2
        row = clean[20].isnan().tolist()
        self.assertTrue(all(row[30 - margin : 30]))
        self.assertTrue(all(row[10 - margin : 10 + margin]))
        self.assertFalse(row[20])

    def test_flat_surface_is_untouched(self):
        clean = remove_edges(torch.full((40, 60), 300.0))
        self.assertEqual(int(clean.isnan().sum()), 0)


class Volume(unittest.TestCase):
    def test_voxels_map_to_storage_and_missing_ones_do_not(self):
        points = np.array([[10.0, 10.0, 10.0], [300.0, 10.0, 10.0]], dtype=np.float32)
        volume = SparseVolume(points, 1.0, torch.device("cpu"))
        cells = torch.tensor([[10, 10, 10], [11, 10, 10], [150, 10, 10]])
        index = volume.locate(cells)
        self.assertGreaterEqual(int(index[0]), 0)
        self.assertEqual(int(index[1]), int(index[0]) + BRICK_VOXELS**2)
        self.assertEqual(int(index[2]), -1)

    def test_mesh_extraction_finds_a_flat_surface(self):
        volume = SparseVolume(np.array([[40.0, 40.0, 40.0]], dtype=np.float32), 1.0, torch.device("cpu"))
        grid = torch.cartesian_prod(*(torch.arange(BRICK_VOXELS) + 2 * BRICK_VOXELS,) * 3)
        grid = grid[(grid >= 2 * BRICK_VOXELS).all(1)]
        index = volume.locate(grid)
        keep = index >= 0
        distance = ((40 - grid[keep, 2]).float() / 4).clamp(-1, 1)
        volume.sdf[index[keep]] = distance * 10
        volume.weight[index[keep]] = 10
        volume.colour[index[keep]] = 200.0
        volume.colour_weight[index[keep]] = 1.0
        vertices, normals, colours, faces = extract_mesh(volume)
        self.assertGreater(len(faces), 100)
        self.assertAlmostEqual(float(np.median(vertices[:, 2])), 40.0, delta=0.6)
        self.assertEqual(int(np.median(colours)), 200)


if __name__ == "__main__":
    unittest.main()
