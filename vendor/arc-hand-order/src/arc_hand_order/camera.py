"""Gameplay camera: aspect-dependent reset pose and matrices.

World frame (right-handed): x to screen right, y up, z toward the viewer. The judgement line is z=0 and notes
approach from negative z. Lane ``n`` is centred at ``4.25*(n-2.5)``.

The reset pose interpolates between a 16:9 and a 4:3 preset by screen aspect.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

LANE_WIDTH = 4.25
SKY_Y0 = 1.0
SKY_Y1 = 5.5
TRACK_FORWARD = 100.0
TRACK_BACKWARD = 53.5
NEAR, FAR = 0.3, 600.0


def lane_x(lane: float) -> float:
    return LANE_WIDTH * (lane - 2.5)


def aff_x(x: float) -> float:
    return 2.0 * LANE_WIDTH * x - LANE_WIDTH


def aff_y(y: float) -> float:
    return SKY_Y0 + (SKY_Y1 - SKY_Y0) * y


def aspect_adjustment(width: float, height: float) -> float:
    """0 at 16:9, 1 at 4:3; unclamped."""
    return (height / (width / 16.0) - 9.0) / 3.0


@dataclass(frozen=True)
class CameraPose:
    position: np.ndarray
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray
    fov_y_deg: float
    aspect: float

    def view_projection(self) -> np.ndarray:
        view = np.identity(4)
        view[0, :3], view[1, :3], view[2, :3] = self.right, self.up, -self.forward
        view[:3, 3] = -view[:3, :3] @ self.position
        f = 1.0 / math.tan(math.radians(self.fov_y_deg) / 2.0)
        proj = np.zeros((4, 4))
        proj[0, 0], proj[1, 1] = f / self.aspect, f
        proj[2, 2], proj[2, 3] = (FAR + NEAR) / (NEAR - FAR), 2 * FAR * NEAR / (NEAR - FAR)
        proj[3, 2] = -1.0
        return proj @ view

    def project(self, point, width: float, height: float) -> tuple[float, float]:
        """World point to pixel coordinates, origin top-left."""
        clip = self.view_projection() @ np.array([*point, 1.0])
        return ((clip[0] / clip[3] + 1) * width / 2, (1 - clip[1] / clip[3]) * height / 2)

    def ray(self, px: float, py: float, width: float, height: float) -> np.ndarray:
        """Direction from the eye through a pixel (not normalised)."""
        t = math.tan(math.radians(self.fov_y_deg) / 2.0)
        return (self.forward + self.right * ((2 * px / width - 1) * t * self.aspect)
                + self.up * ((1 - 2 * py / height) * t))

    def judgement_plane_point(self, px: float, py: float, width: float, height: float):
        """Inverse projection of a pixel onto the vertical plane z=0 that sky input is read on."""
        ray = self.ray(px, py, width, height)
        if ray[2] >= -1e-12:
            raise ValueError("pixel does not see the judgement plane")
        return self.position - self.position[2] / ray[2] * ray

    def ground_point(self, px: float, py: float, width: float, height: float, plane_y: float = 0.0):
        """Inverse projection of a pixel onto the horizontal plane y=plane_y."""
        ray = self.ray(px, py, width, height)
        if abs(ray[1]) < 1e-12:
            raise ValueError("pixel ray is parallel to the plane")
        s = (plane_y - self.position[1]) / ray[1]
        if s <= 0:
            raise ValueError("pixel does not see the plane")
        return self.position + s * ray


def reset_pose(width: float, height: float) -> CameraPose:
    adj = aspect_adjustment(width, height)
    position = np.array([0.0, 9.0, 9.0 - adj])
    forward = np.array([0.0, -5.5, -20.0]) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return CameraPose(position, right, up, forward, 50.0 + 15.0 * adj, width / height)
