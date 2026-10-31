"""Scanner geometry: depth decoding and factory calibration.

The values are from one Ferret Pro and are documented in docs/protocol/. Other scanners will
need their own read from the device.
"""
import torch

WIDTH, HEIGHT = 1280, 800
DEPTH_BYTES = WIDTH * HEIGHT * 14 // 8

UNIT = 0.5
BASELINE = 149.907
FOCAL = 956.7727
CENTRE_X = 645.56445
CENTRE_Y = 401.40128

CLOSE_MODE = "Close_range_mode Default"
LONG_MODE = "Long_range_mode Default"
MODE_OFFSETS = {CLOSE_MODE: 218.0, LONG_MODE: 100.0}

COLOUR_SIZE = (1920, 1080)
COLOUR_FOCAL_X = 839.90887 * 1.5
COLOUR_FOCAL_Y = 839.55103 * 1.5
COLOUR_CX = 655.0 * 1.5  # measured, see docs/protocol/camera-layout.md
COLOUR_CY = 366.0 * 1.5  # measured
K1, K2, K3 = 0.11165, -0.33142, 0.25619
P1, P2 = 0.00076, 0.00035
ROTATION = ((1.0, 0.00237, 0.00183), (-0.00237, 1.0, 0.00058), (-0.00183, -0.00059, 1.0))
TRANSLATION_MM = (19.47028, -0.18568, -0.66741)


def depth_limits(mode):
    """Near and far limits in mm that the 14-bit depth value can represent."""
    offset = MODE_OFFSETS[mode]
    return value_to_mm(16383.0, offset), value_to_mm(0.0, offset)


def value_to_mm(value, offset):
    return UNIT * BASELINE * FOCAL / (value / 64.0 + offset)


def decode_y14(raw):
    """Unpacks big-endian 14-bit pixels from a uint8 tensor. 7 bytes hold 4 pixels."""
    b = raw[: DEPTH_BYTES].reshape(-1, 7).to(torch.int32).T
    pixels = torch.stack(
        (
            b[0] << 6 | b[1] >> 2,
            (b[1] & 0x03) << 12 | b[2] << 4 | b[3] >> 4,
            (b[3] & 0x0F) << 10 | b[4] << 2 | b[5] >> 6,
            (b[5] & 0x3F) << 8 | b[6],
        ),
        dim=1,
    )
    return pixels.reshape(HEIGHT, WIDTH)


def to_depth_mm(values, offset):
    """Depth in mm as float32, with NaN where the scanner returned no depth."""
    depth = UNIT * BASELINE * FOCAL / (values.to(torch.float32) / 64.0 + offset)
    return torch.where(values > 0, depth, torch.tensor(float("nan"), device=values.device))


def project_to_colour(points):
    """Maps depth-camera points (x right, y down, z forward) to 1920x1080 colour pixels."""
    rotation = torch.tensor(ROTATION, dtype=points.dtype, device=points.device)
    translation = torch.tensor(TRANSLATION_MM, dtype=points.dtype, device=points.device)
    q = (points - translation) @ rotation
    a, b = q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]
    r2 = a * a + b * b
    radial = 1 + K1 * r2 + K2 * r2**2 + K3 * r2**3
    u = a * radial + 2 * P1 * a * b + P2 * (r2 + 2 * a * a)
    v = b * radial + P1 * (r2 + 2 * b * b) + 2 * P2 * a * b
    return COLOUR_CX + COLOUR_FOCAL_X * u, COLOUR_CY + COLOUR_FOCAL_Y * v
