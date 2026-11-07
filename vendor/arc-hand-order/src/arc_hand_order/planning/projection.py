"""Gameplay camera projection into the physical tablet's local XY plane.

Judgement coordinates use 100 game units per world unit. Device XY is metres, centred on the screen, with positive y
upwards. The projection uses the physical screen aspect, independent of pixel resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from arc_hand_order.camera import CameraPose, lane_x, reset_pose
from arc_hand_order.schemas.device import DeviceProfile

GAME_UNITS_PER_WORLD_UNIT = 100.0


@dataclass(frozen=True)
class GameplayProjection:
    device: DeviceProfile
    camera: CameraPose = field(init=False, repr=False)
    width_m: float = field(init=False)
    height_m: float = field(init=False)

    def __post_init__(self) -> None:
        width, height = self.device.screen.width_m, self.device.screen.height_m
        object.__setattr__(self, "width_m", width)
        object.__setattr__(self, "height_m", height)
        object.__setattr__(self, "camera", reset_pose(width, height))

    def _world_to_device(self, point: tuple[float, float, float]) -> tuple[float, float]:
        world = np.asarray(point, dtype=float)
        if not np.all(np.isfinite(world)):
            raise ValueError("projection coordinates must be finite")
        if float(np.dot(world - self.camera.position, self.camera.forward)) <= 0:
            raise ValueError("point lies behind the gameplay camera")
        px, py = self.camera.project(world, self.width_m, self.height_m)
        return float(px - self.width_m / 2), float(self.height_m / 2 - py)

    def judgement_to_device(self, x: float, y: float) -> tuple[float, float]:
        """Unclamped judgement-plane (x,y) in game units to local screen metres."""
        return self._world_to_device((x / GAME_UNITS_PER_WORLD_UNIT,
                                      y / GAME_UNITS_PER_WORLD_UNIT, 0.0))

    def ground_to_device(self, x: float, z: float) -> tuple[float, float]:
        """Track-plane (x,z) in game units to local screen metres."""
        return self._world_to_device((x / GAME_UNITS_PER_WORLD_UNIT, 0.0,
                                      z / GAME_UNITS_PER_WORLD_UNIT))

    def lane_center_to_device(self, lane: int) -> tuple[float, float]:
        """Centre of lane 1..4 on the judgement line, before lane hitbox rules."""
        if isinstance(lane, bool) or lane not in (1, 2, 3, 4):
            raise ValueError("lane must be 1, 2, 3 or 4")
        return self.ground_to_device(GAME_UNITS_PER_WORLD_UNIT * lane_x(lane), 0.0)

    def _device_pixel(self, x_m: float, y_m: float) -> tuple[float, float]:
        if not math.isfinite(x_m) or not math.isfinite(y_m):
            raise ValueError("device coordinates must be finite")
        return x_m + self.width_m / 2, self.height_m / 2 - y_m

    def device_to_judgement(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Screen metres to unclamped sky input (x,y), in game units."""
        px, py = self._device_pixel(x_m, y_m)
        point = self.camera.judgement_plane_point(px, py, self.width_m, self.height_m)
        return tuple(float(value * GAME_UNITS_PER_WORLD_UNIT) for value in point[:2])

    def device_to_ground(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Screen metres to track input (x,z), in game units; rejects unseen plane."""
        px, py = self._device_pixel(x_m, y_m)
        point = self.camera.ground_point(px, py, self.width_m, self.height_m)
        return float(point[0] * GAME_UNITS_PER_WORLD_UNIT), float(point[2] * GAME_UNITS_PER_WORLD_UNIT)

    def judgement_box_to_device(
        self, lower_xy: tuple[float, float], upper_xy: tuple[float, float],
    ) -> tuple[tuple[float, float], ...]:
        """Exact four projected corners of an unclamped game-plane rectangle.

        Returns lower-left, lower-right, upper-right, upper-left, preserving
        edges under perspective. Does not clip to the screen, clamp sky input,
        expand to an axis-aligned envelope, or decide open hitbox boundaries.
        Those rules remain the caller's responsibility; this polygon alone
        does not describe a sky note's height-clamped region (``rules``).
        """
        x0, y0 = lower_xy
        x1, y1 = upper_xy
        if not x0 < x1 or not y0 < y1:
            raise ValueError("judgement rectangle bounds must be ordered")
        return tuple(self.judgement_to_device(x, y)
                     for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
