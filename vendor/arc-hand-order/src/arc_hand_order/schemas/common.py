"""Shared primitives for the strict JSON models: no extra fields, no NaN or infinity."""

import math
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain a non-whitespace character")
    return value


NonEmptyString = Annotated[str, StringConstraints(min_length=1), AfterValidator(nonblank)]
PositiveFloat = Annotated[FiniteFloat, Field(gt=0)]
SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def check_finite_tree(value: object) -> None:
    """Reject non-finite floats even inside a raw JSON payload."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and infinity are not permitted")
    if isinstance(value, dict):
        for nested in value.values():
            check_finite_tree(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            check_finite_tree(nested)


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False,
                              validate_default=True, revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def finite_input(cls, data):
        check_finite_tree(data)
        return data


class VersionedModel(ContractModel):
    schema_version: Literal["1.0"]
