"""The desktop interface."""
import os

# Qt's Wayland backend only offered OpenGL ES on NVIDIA here, so run through XWayland.
# PyOpenGL must then look for a GLX context. Both must be set before their libraries load.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("PYOPENGL_PLATFORM", "glx")
