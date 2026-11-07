"""The parsed chart (ChartIR) the planner reads. Event times are integer chart milliseconds."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from .common import ContractModel, NonEmptyString, PositiveFloat, SHA256, VersionedModel

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
XY = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


class SourceLocation(ContractModel):
    file: NonEmptyString
    line: PositiveInt
    column: PositiveInt


class ChartEvent(ContractModel):
    event_id: NonEmptyString
    source: SourceLocation
    start_time_ms: int
    end_time_ms: int
    timinggroup_id: NonEmptyString

    @model_validator(mode="after")
    def ordered(self) -> ChartEvent:
        if self.end_time_ms < self.start_time_ms:
            raise ValueError("end_time_ms must be at or after start_time_ms")
        return self


class TapEvent(ChartEvent):
    type: Literal["tap"]
    lane: Annotated[int, Field(ge=1, le=4)]

    @model_validator(mode="after")
    def instantaneous(self) -> TapEvent:
        if self.start_time_ms != self.end_time_ms:
            raise ValueError("a tap is instantaneous")
        return self


class HoldEvent(ChartEvent):
    type: Literal["hold"]
    lane: Annotated[int, Field(ge=1, le=4)]

    @model_validator(mode="after")
    def positive_duration(self) -> HoldEvent:
        if self.start_time_ms == self.end_time_ms:
            raise ValueError("a hold requires positive duration")
        return self


class ArcChain(ContractModel):
    """The links chosen for an arc; they are directed and need not be reciprocal at forks."""

    previous_event_id: NonEmptyString | None = None
    next_event_id: NonEmptyString | None = None


class ArcEvent(ChartEvent):
    type: Literal["arc"]
    start_aff_xy: XY
    end_aff_xy: XY
    interpolation: NonEmptyString
    color: NonNegativeInt
    effect: NonEmptyString
    is_guideline: bool
    is_designant: bool = False
    # Every arc whose end meets this one's start or whose start meets its end; chain: the links chosen of them.
    connected_event_ids: list[NonEmptyString]
    chain: ArcChain | None = None


class ArcTapEvent(ChartEvent):
    type: Literal["arctap"]
    parent_arc_id: NonEmptyString

    @model_validator(mode="after")
    def instantaneous(self) -> ArcTapEvent:
        if self.start_time_ms != self.end_time_ms:
            raise ValueError("an arctap is instantaneous")
        return self


class TimingEvent(ChartEvent):
    type: Literal["timing"]
    bpm: FiniteFloat
    beats_per_line: PositiveFloat

    @model_validator(mode="after")
    def instantaneous(self) -> TimingEvent:
        if self.start_time_ms != self.end_time_ms:
            raise ValueError("a timing directive is instantaneous")
        return self


class RawAFFEvent(ChartEvent):
    """Preserved AFF syntax whose gameplay meaning is not supported."""

    type: Literal["raw_unsupported"]
    instruction: NonEmptyString
    raw_text: NonEmptyString
    unsupported_reason: NonEmptyString


class SceneControlEvent(ChartEvent):
    """Scene commands kept with their arguments. None of them changes input or judgement: arcahvdistort, arcahvdebris
    and redline are visual effects, and hidegroup switches its timing group's visibility."""

    type: Literal["scenecontrol"]
    name: Literal["arcahvdistort", "arcahvdebris", "redline", "hidegroup"]
    arguments: Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]
    raw_text: NonEmptyString

    @model_validator(mode="after")
    def instantaneous(self) -> SceneControlEvent:
        if self.start_time_ms != self.end_time_ms:
            raise ValueError("a scenecontrol command has one invocation time")
        if self.arguments[0] < 0:
            raise ValueError("the scenecontrol duration must be nonnegative")
        return self


AFFEvent = Annotated[
    TapEvent | HoldEvent | ArcEvent | ArcTapEvent | TimingEvent | SceneControlEvent | RawAFFEvent,
    Field(discriminator="type"),
]


class TimingGroup(ContractModel):
    group_id: NonEmptyString
    parent_group_id: NonEmptyString | None
    raw_attributes: list[NonEmptyString]
    semantics_status: Literal["supported", "unsupported", "unknown"]


class ChartIR(VersionedModel):
    chart_sha256: SHA256
    audio_offset_ms: int
    source_time_unit: Literal["ms"]
    coordinate_unit: Literal["aff"]
    timinggroups: Annotated[list[TimingGroup], Field(min_length=1)]
    events: list[AFFEvent]

    @model_validator(mode="after")
    def references(self) -> ChartIR:
        _unique([event.event_id for event in self.events], "event IDs")
        _unique([group.group_id for group in self.timinggroups], "timinggroup IDs")
        groups = {group.group_id for group in self.timinggroups}
        for group in self.timinggroups:
            if group.parent_group_id is not None and group.parent_group_id not in groups:
                raise ValueError("timinggroup parent does not exist")
            if group.parent_group_id == group.group_id:
                raise ValueError("timinggroup cannot be its own parent")
        events = {event.event_id: event for event in self.events}
        for event in self.events:
            if event.timinggroup_id not in groups:
                raise ValueError("event refers to an unknown timinggroup")
            if isinstance(event, ArcTapEvent):
                parent = events.get(event.parent_arc_id)
                if not isinstance(parent, ArcEvent):
                    raise ValueError("arctap must refer to a known Arc parent")
                if event.timinggroup_id != parent.timinggroup_id:
                    raise ValueError("arctap and parent must have the same timinggroup")
                if not parent.start_time_ms <= event.start_time_ms <= parent.end_time_ms:
                    raise ValueError("arctap time lies outside its parent Arc")
            if isinstance(event, ArcEvent):
                _unique(event.connected_event_ids, "connected Arc IDs")
                if any(not isinstance(events.get(other), ArcEvent) or other == event.event_id
                       for other in event.connected_event_ids):
                    raise ValueError("Arc connections must refer to other known Arcs")
                if event.chain is not None:
                    for linked in (event.chain.previous_event_id, event.chain.next_event_id):
                        if linked is not None and linked not in event.connected_event_ids:
                            raise ValueError("Selected Arc links must be recorded connection candidates")
        for group in self.timinggroups:
            visited: set[str] = set()
            cursor: str | None = group.group_id
            parents = {item.group_id: item.parent_group_id for item in self.timinggroups}
            while cursor is not None:
                if cursor in visited:
                    raise ValueError("timinggroup parent cycle")
                visited.add(cursor)
                cursor = parents[cursor]
        return self
