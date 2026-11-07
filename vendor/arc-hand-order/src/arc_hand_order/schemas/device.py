"""The device profile: the tablet's screen in landscape, from which the gameplay camera places the judgement line,
lanes and sky area (planning/projection.py)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .common import ContractModel, NonEmptyString, PositiveFloat, VersionedModel


class ScreenGeometry(ContractModel):
    width_m: PositiveFloat  # the lit area, landscape
    height_m: PositiveFloat
    pixel_width: Annotated[int, Field(gt=0)]
    pixel_height: Annotated[int, Field(gt=0)]


class DeviceProfile(VersionedModel):
    profile_id: NonEmptyString
    description: NonEmptyString
    screen: ScreenGeometry
