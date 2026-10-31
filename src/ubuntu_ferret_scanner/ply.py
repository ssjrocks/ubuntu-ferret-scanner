"""Binary PLY files for coloured clouds and meshes, and STL for printing.

Stored points use y up and z towards the viewer: a half turn from the camera axes, so shapes
are not mirrored.
"""
import numpy as np

CLOUD = np.dtype([("xyz", "<f4", 3), ("rgb", "u1", 3)])
VERTEX = np.dtype([("xyz", "<f4", 3), ("normal", "<f4", 3), ("rgb", "u1", 3)])
FACE = np.dtype([("count", "u1"), ("index", "<i4", 3)])


def camera_to_view(points):
    return points * np.array([1, -1, -1], dtype=points.dtype)


def write_cloud(path, points, colours):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    data = np.empty(len(points), dtype=CLOUD)
    data["xyz"], data["rgb"] = points, colours
    with open(path, "wb") as f:
        f.write(header.encode())
        data.tofile(f)


def write_mesh(path, vertices, normals, colours, faces):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(vertices)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    )
    data = np.empty(len(vertices), dtype=VERTEX)
    data["xyz"], data["normal"], data["rgb"] = vertices, normals, colours
    face_data = np.empty(len(faces), dtype=FACE)
    face_data["count"], face_data["index"] = 3, faces
    with open(path, "wb") as f:
        f.write(header.encode())
        data.tofile(f)
        face_data.tofile(f)


def _skip_header(f):
    counts = {}
    while True:
        line = f.readline().decode().strip()
        if line.startswith("element"):
            _, name, count = line.split()
            counts[name] = int(count)
        if line == "end_header":
            return counts


def read_cloud(path):
    with open(path, "rb") as f:
        counts = _skip_header(f)
        data = np.fromfile(f, dtype=CLOUD, count=counts["vertex"])
    return data["xyz"], data["rgb"]


def read_mesh(path):
    with open(path, "rb") as f:
        counts = _skip_header(f)
        vertices = np.fromfile(f, dtype=VERTEX, count=counts["vertex"])
        faces = np.fromfile(f, dtype=FACE, count=counts["face"])
    return vertices["xyz"], vertices["normal"], vertices["rgb"], faces["index"]


def write_stl(path, vertices, faces):
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(length, 1e-12)
    record = np.dtype([("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])
    data = np.empty(len(faces), dtype=record)
    data["normal"], data["corners"], data["attribute"] = normals, triangles, 0
    with open(path, "wb") as f:
        f.write(b"Ubuntu Ferret Scanner".ljust(80, b"\0"))
        f.write(np.array([len(faces)], dtype="<u4").tobytes())
        data.tofile(f)
