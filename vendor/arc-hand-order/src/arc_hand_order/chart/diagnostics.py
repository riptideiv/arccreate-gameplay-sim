"""Location-aware diagnostics shared by AFF parsing and task construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from arc_hand_order.schemas.chart_ir import SourceLocation


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: Literal["error", "warning", "info"] = "error"
    source: SourceLocation | None = None
    event_id: str | None = None
    blocks_task_generation: bool = True

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "source": self.source.model_dump(mode="json") if self.source else None,
            "event_id": self.event_id,
            "blocks_task_generation": self.blocks_task_generation,
        }

    def model_dump(self, **_kwargs) -> dict:
        """Match the JSON export interface used by the surrounding contracts."""
        return self.to_dict()
