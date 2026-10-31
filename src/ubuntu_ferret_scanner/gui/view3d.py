"""OpenGL view of the live cloud, finished cloud and meshes, with orbit controls."""
import ctypes

import numpy as np
from OpenGL import GL
from PyQt6.QtCore import Qt
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

POINT_VERTEX = """#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 colour;
uniform mat4 matrix;
uniform float point_size;
out vec3 shade;
void main() {
    gl_Position = matrix * vec4(position, 1.0);
    gl_PointSize = point_size;
    shade = colour;
}"""

POINT_FRAGMENT = """#version 330 core
in vec3 shade;
out vec4 result;
void main() { result = vec4(shade, 1.0); }"""

MESH_VERTEX = """#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 normal;
layout(location = 2) in vec3 colour;
uniform mat4 matrix;
uniform mat4 view;
out vec3 view_normal;
out vec3 shade;
void main() {
    gl_Position = matrix * vec4(position, 1.0);
    view_normal = mat3(view) * normal;
    shade = colour;
}"""

MESH_FRAGMENT = """#version 330 core
in vec3 view_normal;
in vec3 shade;
uniform float use_colour;
out vec4 result;
void main() {
    vec3 n = normalize(view_normal);
    vec3 base = mix(vec3(0.72), shade, use_colour);
    float light = 0.25 + 0.75 * abs(dot(n, normalize(vec3(0.3, 0.5, 0.8))));
    float flat_colour = use_colour * 0.55;
    result = vec4(base * mix(light, 1.0, flat_colour), 1.0);
}"""

POINT_BYTES = 15
MESH_BYTES = 27


def compile_program(vertex, fragment):
    program = GL.glCreateProgram()
    for source, kind in ((vertex, GL.GL_VERTEX_SHADER), (fragment, GL.GL_FRAGMENT_SHADER)):
        shader = GL.glCreateShader(kind)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
        GL.glAttachShader(program, shader)
    GL.glLinkProgram(program)
    if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(program).decode())
    return program


def perspective(fov_degrees, aspect, near, far):
    f = 1 / np.tan(np.radians(fov_degrees) / 2)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def orbit_view(yaw, pitch, distance, target):
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy, 0], [0, 1, 0, 0], [-sy, 0, cy, 0], [0, 0, 0, 1]], dtype=np.float32)
    Rx = np.array([[1, 0, 0, 0], [0, cp, -sp, 0], [0, sp, cp, 0], [0, 0, 0, 1]], dtype=np.float32)
    move = np.eye(4, dtype=np.float32)
    move[:3, 3] = -np.asarray(target, dtype=np.float32)
    back = np.eye(4, dtype=np.float32)
    back[2, 3] = -distance
    return back @ Rx @ Ry @ move


class View3D(QOpenGLWidget):
    def __init__(self, max_points, parent=None):
        super().__init__(parent)
        self.max_points = max_points
        self.mode = "points"
        self.yaw, self.pitch = 0.0, 0.0
        self.distance = 700.0
        self.target = np.array([0.0, 0.0, -400.0])
        self.drag_button = None
        self.drag_from = None
        self.point_size = 2.0
        self.pending = []
        self.point_count = 0
        self.index_count = 0
        self.new_mesh = None
        self.new_cloud = None
        self.clear_points = False
        self.sum_points = np.zeros(3)
        self.ready = False
        self.scanner_position = None
        self.setMinimumSize(480, 360)

    def initializeGL(self):
        GL.glClearColor(0.09, 0.10, 0.12, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        self.point_program = compile_program(POINT_VERTEX, POINT_FRAGMENT)
        self.mesh_program = compile_program(MESH_VERTEX, MESH_FRAGMENT)

        self.point_vao = GL.glGenVertexArrays(1)
        self.point_vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.point_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.point_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.max_points * POINT_BYTES, None, GL.GL_DYNAMIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, POINT_BYTES, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_UNSIGNED_BYTE, True, POINT_BYTES, ctypes.c_void_p(12))

        self.mesh_vao = GL.glGenVertexArrays(1)
        self.mesh_vbo = GL.glGenBuffers(1)
        self.mesh_ebo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.mesh_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.mesh_vbo)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.mesh_ebo)
        GL.glBindVertexArray(0)
        self.ready = True

    def resizeGL(self, width, height):
        GL.glViewport(0, 0, width, height)

    def add_points(self, points, colours):
        self.pending.append((points, colours))
        self.update()

    def reset_points(self):
        self.pending.clear()
        self.clear_points = True
        self.update()

    def set_cloud(self, points, colours):
        self.new_cloud = (points[: self.max_points], colours[: self.max_points])
        self.fit(points)
        self.mode = "points"
        self.update()

    def set_mesh(self, vertices, normals, colours, faces):
        self.new_mesh = (vertices, normals, colours, faces)
        self.fit(vertices)
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def fit(self, points):
        low = np.percentile(points[:: max(len(points) // 100000, 1)], 1, axis=0)
        high = np.percentile(points[:: max(len(points) // 100000, 1)], 99, axis=0)
        self.target = (low + high) / 2
        self.distance = max(float(np.linalg.norm(high - low)) * 0.9, 100.0)

    def upload_points(self, points, colours, start):
        data = np.empty(len(points), dtype=[("p", "<f4", 3), ("c", "u1", 3)])
        data["p"], data["c"] = points, colours
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.point_vbo)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, start * POINT_BYTES, data.nbytes, data)

    def flush(self):
        if self.clear_points:
            self.point_count = 0
            self.sum_points[:] = 0
            self.clear_points = False
        if self.new_cloud is not None:
            points, colours = self.new_cloud
            self.upload_points(points, colours, 0)
            self.point_count = len(points)
            self.new_cloud = None
        for points, colours in self.pending:
            room = self.max_points - self.point_count
            if room <= 0:
                break
            points, colours = points[:room], colours[:room]
            self.upload_points(points, colours, self.point_count)
            self.point_count += len(points)
            self.sum_points += points.sum(axis=0)
            if self.mode == "points" and self.point_count:
                self.target = self.target * 0.9 + self.sum_points / self.point_count * 0.1
        self.pending.clear()
        if self.new_mesh is not None:
            vertices, normals, colours, faces = self.new_mesh
            data = np.empty(len(vertices), dtype=[("p", "<f4", 3), ("n", "<f4", 3), ("c", "u1", 3)])
            data["p"], data["n"], data["c"] = vertices, normals, colours
            GL.glBindVertexArray(self.mesh_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.mesh_vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.mesh_ebo)
            indices = faces.astype(np.uint32).ravel()
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, MESH_BYTES, ctypes.c_void_p(0))
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, False, MESH_BYTES, ctypes.c_void_p(12))
            GL.glEnableVertexAttribArray(2)
            GL.glVertexAttribPointer(2, 3, GL.GL_UNSIGNED_BYTE, True, MESH_BYTES, ctypes.c_void_p(24))
            GL.glBindVertexArray(0)
            self.index_count = len(indices)
            self.new_mesh = None

    def paintGL(self):
        self.flush()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        aspect = max(self.width(), 1) / max(self.height(), 1)
        view = orbit_view(self.yaw, self.pitch, self.distance, self.target)
        projection = perspective(45, aspect, max(self.distance / 100, 1), self.distance * 20)
        matrix = projection @ view

        if self.mode == "points":
            GL.glUseProgram(self.point_program)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.point_program, "matrix"), 1, True, matrix)
            GL.glUniform1f(GL.glGetUniformLocation(self.point_program, "point_size"), self.point_size)
            GL.glBindVertexArray(self.point_vao)
            GL.glDrawArrays(GL.GL_POINTS, 0, self.point_count)
        elif self.index_count:
            GL.glUseProgram(self.mesh_program)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.mesh_program, "matrix"), 1, True, matrix)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.mesh_program, "view"), 1, True, view)
            GL.glUniform1f(GL.glGetUniformLocation(self.mesh_program, "use_colour"),
                           1.0 if self.mode == "textured" else 0.0)
            GL.glBindVertexArray(self.mesh_vao)
            GL.glDrawElements(GL.GL_TRIANGLES, self.index_count, GL.GL_UNSIGNED_INT, None)
        GL.glBindVertexArray(0)

    def mousePressEvent(self, event):
        self.drag_button = event.button()
        self.drag_from = event.position()

    def mouseReleaseEvent(self, event):
        self.drag_button = None

    def mouseMoveEvent(self, event):
        if self.drag_from is None or self.drag_button is None:
            return
        move = event.position() - self.drag_from
        self.drag_from = event.position()
        if self.drag_button == Qt.MouseButton.LeftButton:
            self.yaw += move.x() * 0.008
            self.pitch = float(np.clip(self.pitch + move.y() * 0.008, -1.55, 1.55))
        else:
            scale = self.distance / max(self.height(), 1)
            inverse = np.linalg.inv(orbit_view(self.yaw, self.pitch, self.distance, self.target))
            self.target = self.target - inverse[:3, 0] * move.x() * scale + inverse[:3, 1] * move.y() * scale
        self.update()

    def wheelEvent(self, event):
        self.distance = float(np.clip(self.distance * 0.999 ** event.angleDelta().y(), 30.0, 20000.0))
        self.update()
