"""Lossless, typed syntax tree for AFF, independent of gameplay semantics.

Arguments retain their lexeme and source location even when a known command has
invalid arguments. Unsupported headers and commands therefore remain reviewable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from arc_hand_order.schemas.chart_ir import SourceLocation


@dataclass(frozen=True)
class ScalarSyntax:
    value: int | float | str | bool
    raw_text: str
    source: SourceLocation
    kind: Literal["number", "string", "identifier"]


@dataclass(frozen=True)
class HeaderSyntax:
    name: str
    value: str
    raw_text: str
    source: SourceLocation


@dataclass(frozen=True)
class CommandSyntax:
    instruction: str
    arguments: tuple[ScalarSyntax, ...]
    raw_text: str
    source: SourceLocation


@dataclass(frozen=True)
class TapSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class HoldSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class TimingSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class ArcTapSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class ArcSyntax(CommandSyntax):
    arctaps: tuple[ArcTapSyntax, ...] = ()


@dataclass(frozen=True)
class CameraSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class SceneControlSyntax(CommandSyntax):
    pass


@dataclass(frozen=True)
class UnknownSyntax(CommandSyntax):
    canonical_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimingGroupSyntax(CommandSyntax):
    children: tuple[CommandSyntax, ...] = ()


@dataclass(frozen=True)
class AFFSyntax:
    headers: tuple[HeaderSyntax, ...] = ()
    statements: tuple[CommandSyntax, ...] = ()
    source_file: str = "<memory>"
