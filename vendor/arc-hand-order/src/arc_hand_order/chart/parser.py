"""AFF lexer, recursive descent parser and conservative ChartIR conversion.

Unknown commands use balanced delimiter parsing, preserving their raw syntax;
known malformed commands remain errors rather than becoming unsupported events.
IDs derive from semantic content and group identity, never file paths or lines.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from pydantic import ValidationError

from arc_hand_order.schemas.chart_ir import (
    ArcEvent, ArcTapEvent, ChartIR, HoldEvent, RawAFFEvent, SceneControlEvent, SourceLocation,
    TapEvent, TimingEvent, TimingGroup,
)

from .diagnostics import Diagnostic
from .arc_connections import connect_arcs
from .syntax import (
    AFFSyntax, ArcSyntax, ArcTapSyntax, CameraSyntax, CommandSyntax, HeaderSyntax,
    HoldSyntax, ScalarSyntax, SceneControlSyntax, TapSyntax, TimingGroupSyntax,
    TimingSyntax, UnknownSyntax,
)

SUPPORTED_CURVES = frozenset({"s", "b", "si", "so", "sisi", "siso", "sosi", "soso"})
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_KNOWN = {
    "hold": HoldSyntax, "timing": TimingSyntax, "arc": ArcSyntax,
    "camera": CameraSyntax, "scenecontrol": SceneControlSyntax,
    "timinggroup": TimingGroupSyntax, "arctap": ArcTapSyntax,
}


@dataclass(frozen=True)
class ParsedChart:
    chart: ChartIR | None
    diagnostics: list[Diagnostic]
    ast: AFFSyntax
    features: list[str]

    @property
    def blocks_task_generation(self) -> bool:
        return self.chart is None or any(item.blocks_task_generation for item in self.diagnostics)


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int
    line: int
    column: int


class _SyntaxError(Exception):
    def __init__(self, message: str, token: _Token):
        self.message = message
        self.token = token
        super().__init__(message)


def _lex(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index, line, column = 0, 1, 1
    if text.startswith("\ufeff"):
        index = 1
    punctuation = "()[]{},;:"
    while index < len(text):
        char = text[index]
        if char in " \t\v\f":
            index += 1
            column += 1
            continue
        if char in "\r\n":
            end = index + (2 if text.startswith("\r\n", index) else 1)
            tokens.append(_Token("NEWLINE", text[index:end], index, end, line, column))
            index, line, column = end, line + 1, 1
            continue
        if text.startswith("//", index) or char == "#":
            while index < len(text) and text[index] not in "\r\n":
                index += 1
                column += 1
            continue
        if text.startswith("/*", index):
            start, start_line, start_column = index, line, column
            index, column = index + 2, column + 2
            while index < len(text) and not text.startswith("*/", index):
                if text[index] in "\r\n":
                    index += 2 if text.startswith("\r\n", index) else 1
                    line, column = line + 1, 1
                else:
                    index, column = index + 1, column + 1
            if index >= len(text):
                raise _SyntaxError("Unterminated block comment", _Token("COMMENT", "/*", start, start + 2, start_line, start_column))
            index, column = index + 2, column + 2
            continue
        start, start_line, start_column = index, line, column
        if char in ('"', "'"):
            quote = char
            index, column = index + 1, column + 1
            while index < len(text):
                if text[index] in "\r\n":
                    raise _SyntaxError("A quoted string cannot contain a literal newline", _Token("STRING", text[start:index], start, index, start_line, start_column))
                if text[index] == "\\":
                    index, column = index + 2, column + 2
                elif text[index] == quote:
                    index, column = index + 1, column + 1
                    break
                else:
                    index, column = index + 1, column + 1
            else:
                raise _SyntaxError("Unterminated quoted string", _Token("STRING", text[start:index], start, index, start_line, start_column))
            if index > len(text):
                raise _SyntaxError("Unterminated quoted string", _Token("STRING", text[start:index], start, index, start_line, start_column))
            tokens.append(_Token("STRING", text[start:index], start, index, start_line, start_column))
        elif char in punctuation:
            tokens.append(_Token(char, char, index, index + 1, line, column))
            index, column = index + 1, column + 1
        else:
            while index < len(text):
                char = text[index]
                if char.isspace() or char in punctuation or char in ('"', "'") or char == "#" or text.startswith("//", index) or text.startswith("/*", index):
                    break
                index, column = index + 1, column + 1
            tokens.append(_Token("ATOM", text[start:index], start, index, start_line, start_column))
    tokens.append(_Token("EOF", "", len(text), len(text), line, column))
    return tokens


def _decode_string(raw: str) -> str:
    # AFF defines escaped quotation marks; retain unfamiliar escapes literally.
    result, index = [], 1
    while index < len(raw) - 1:
        if raw[index] == "\\" and index + 1 < len(raw) - 1 and raw[index + 1] in ("\\", raw[0]):
            result.append(raw[index + 1])
            index += 2
        else:
            result.append(raw[index])
            index += 1
    return "".join(result)


class _Parser:
    def __init__(self, text: str, source_file: str, tokens: list[_Token], diagnostics: list[Diagnostic]):
        self.text, self.source_file, self.tokens, self.diagnostics = text, source_file, tokens, diagnostics
        self.pos = 0

    def source(self, token: _Token) -> SourceLocation:
        return SourceLocation(file=self.source_file, line=token.line, column=token.column)

    def peek(self) -> _Token:
        while self.tokens[self.pos].kind == "NEWLINE":
            self.pos += 1
        return self.tokens[self.pos]

    def take(self, kind: str) -> _Token:
        token = self.peek()
        if token.kind != kind:
            raise _SyntaxError(f"Expected {kind!r}, found {token.text or token.kind!r}", token)
        self.pos += 1
        return token

    def scalar(self) -> ScalarSyntax:
        token = self.peek()
        if token.kind not in {"ATOM", "STRING"}:
            raise _SyntaxError("Expected a scalar argument", token)
        self.pos += 1
        if token.kind == "STRING":
            value, kind = _decode_string(token.text), "string"
        elif _NUMBER.fullmatch(token.text):
            try:
                value = int(token.text) if _INTEGER.fullmatch(token.text) else float(token.text)
            except ValueError:
                raise _SyntaxError("Numeric literal exceeds the parser's supported range", token) from None
            if isinstance(value, float) and not math.isfinite(value):
                raise _SyntaxError("Numeric literals must be finite", token)
            if isinstance(value, float):
                try:
                    exact = Decimal(token.text)
                    exact.as_tuple()
                except InvalidOperation:
                    raise _SyntaxError("Numeric literal exponent exceeds the parser's supported range", token) from None
                if value == 0 and exact != 0:
                    raise _SyntaxError("Numeric literal is too small to represent without underflow", token)
            kind = "number"
        else:
            value = {"true": True, "false": False}.get(token.text, token.text)
            kind = "identifier"
        return ScalarSyntax(value=value, raw_text=token.text, source=self.source(token), kind=kind)

    def arguments(self) -> tuple[ScalarSyntax, ...]:
        self.take("(")
        result = []
        if self.peek().kind != ")":
            result.append(self.scalar())
            while self.peek().kind == ",":
                self.take(",")
                result.append(self.scalar())
        self.take(")")
        return tuple(result)

    def statement(self) -> CommandSyntax:
        start = self.peek()
        if start.kind == "(":
            arguments = self.arguments()
            end = self.take(";")
            return TapSyntax("tap", arguments, self.text[start.start:end.end], self.source(start))
        name = self.take("ATOM")
        if name.text not in _KNOWN:
            return self.unknown(name)
        arguments = self.arguments()
        if name.text == "timinggroup":
            self.take("{")
            children = self.statements(in_group=True)
            self.take("}")
            end = self.take(";")
            return TimingGroupSyntax(name.text, arguments, self.text[start.start:end.end], self.source(start), tuple(children))
        arctaps = []
        if name.text == "arc" and self.peek().kind == "[":
            self.take("[")
            if self.peek().kind != "]":
                while True:
                    tap = self.take("ATOM")
                    if tap.text != "arctap":
                        raise _SyntaxError("Only arctap(...) is valid inside an Arc's child list", tap)
                    child_args = self.arguments()
                    end = self.tokens[self.pos - 1]
                    arctaps.append(ArcTapSyntax("arctap", child_args, self.text[tap.start:end.end], self.source(tap)))
                    if self.peek().kind != ",":
                        break
                    self.take(",")
            self.take("]")
        end = self.take(";")
        common = (name.text, arguments, self.text[start.start:end.end], self.source(start))
        return ArcSyntax(*common, tuple(arctaps)) if name.text == "arc" else _KNOWN[name.text](*common)

    def unknown(self, start: _Token) -> UnknownSyntax:
        self.take("(")
        stack, canonical, args = [")"], [], []
        while stack:
            token = self.peek()
            if token.kind == "EOF":
                raise _SyntaxError("Unterminated unknown directive", token)
            if token.kind in {"(", "[", "{"}:
                stack.append({"(": ")", "[": "]", "{": "}"}[token.kind])
                self.pos += 1
                canonical.append(token.text)
            elif token.kind in {")", "]", "}"}:
                if token.kind != stack[-1]:
                    raise _SyntaxError("Mismatched delimiter in unknown directive", token)
                stack.pop()
                self.pos += 1
                if stack:
                    canonical.append(token.text)
            elif token.kind == ";":
                raise _SyntaxError("Unexpected semicolon inside a directive", token)
            elif token.kind in {"ATOM", "STRING"}:
                scalar = self.scalar()
                args.append(scalar)
                canonical.append(_canonical_scalar(scalar))
            else:
                self.pos += 1
                canonical.append(token.text)
        end = self.take(";")
        return UnknownSyntax(start.text, tuple(args), self.text[start.start:end.end], self.source(start), tuple(canonical))

    def statements(self, in_group: bool = False) -> list[CommandSyntax]:
        statements = []
        while self.peek().kind != "EOF" and not (in_group and self.peek().kind == "}"):
            try:
                statements.append(self.statement())
            except _SyntaxError as error:
                self.diagnostics.append(Diagnostic("aff_syntax_error", error.message, source=self.source(error.token)))
                # Recover to the next statement without swallowing a group's close.
                while self.peek().kind not in {"EOF", ";", "}"}:
                    self.pos += 1
                if self.peek().kind == ";" or (not in_group and self.peek().kind == "}"):
                    self.pos += 1
        return statements

    def document(self) -> AFFSyntax:
        headers = []
        while True:
            token = self.peek()
            if token.kind == "ATOM" and token.text == "-":
                self.pos += 1
                if self.tokens[self.pos].kind not in {"NEWLINE", "EOF"}:
                    raise _SyntaxError("The AFF header separator must occupy its own line", self.tokens[self.pos])
                break
            if token.kind == "EOF":
                raise _SyntaxError("Missing AFF header separator '-'", token)
            name = self.take("ATOM")
            self.take(":")
            begin = self.pos
            while self.tokens[self.pos].kind not in {"NEWLINE", "EOF"}:
                self.pos += 1
            values = self.tokens[begin:self.pos]
            end = values[-1].end if values else self.tokens[self.pos].start
            value = self.text[values[0].start:end] if values else ""
            headers.append(HeaderSyntax(name.text, value.strip(), self.text[name.start:end], self.source(name)))
        return AFFSyntax(tuple(headers), tuple(self.statements()), self.source_file)


def _canonical_scalar(argument: ScalarSyntax) -> str:
    if argument.kind == "number":
        # normalize() applies Decimal's current precision and could merge distinct
        # long integers. Work on the exact decimal tuple without context rounding.
        number = Decimal(argument.raw_text).as_tuple()
        digits = list(number.digits)
        exponent = number.exponent
        if not any(digits):
            return "n:0"
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return "n:" + ("-" if number.sign else "") + "".join(map(str, digits)) + "e" + str(exponent)
    return "v:" + json.dumps(argument.value, ensure_ascii=False, separators=(",", ":"))


class _ConversionError(Exception):
    def __init__(self, message: str, source: SourceLocation):
        self.message, self.source = message, source


class _Converter:
    def __init__(self, ast: AFFSyntax, diagnostics: list[Diagnostic]):
        self.ast, self.diagnostics = ast, diagnostics
        self.occurrences: Counter[str] = Counter()
        self.features: set[str] = set()
        self.groups = [TimingGroup(group_id="default", parent_group_id=None, raw_attributes=[], semantics_status="supported")]
        self.events: list = []

    def identifier(self, kind: str, content: object) -> str:
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        base = f"{kind}_{digest}"
        self.occurrences[base] += 1
        return f"{base}_{self.occurrences[base]}"

    def event_id(self, node: CommandSyntax, group_id: str) -> str:
        content = (group_id, node.instruction, tuple(_canonical_scalar(a) for a in node.arguments))
        if isinstance(node, UnknownSyntax):
            content = (group_id, node.instruction, node.canonical_tokens)
        return self.identifier(node.instruction, content)

    def block(self, code: str, message: str, node: CommandSyntax | HeaderSyntax, event_id: str | None = None) -> None:
        self.diagnostics.append(Diagnostic(code, message, "warning", node.source, event_id, True))

    @staticmethod
    def count(node: CommandSyntax, counts: set[int]) -> None:
        if len(node.arguments) not in counts:
            raise _ConversionError(f"{node.instruction} expects {' or '.join(str(n) for n in sorted(counts))} arguments; got {len(node.arguments)}", node.source)

    @staticmethod
    def integer(node: CommandSyntax, index: int) -> int:
        value = node.arguments[index]
        if value.kind != "number" or not _INTEGER.fullmatch(value.raw_text):
            raise _ConversionError(f"{node.instruction} argument {index + 1} must be an integer", value.source)
        result = int(value.raw_text)
        if not -(2**31) <= result <= 2**31 - 1:
            raise _ConversionError(f"{node.instruction} argument {index + 1} exceeds the signed 32-bit integer range", value.source)
        return result

    @staticmethod
    def number(node: CommandSyntax, index: int) -> float:
        value = node.arguments[index]
        if value.kind != "number" or not math.isfinite(float(value.value)):
            raise _ConversionError(f"{node.instruction} argument {index + 1} must be a finite number", value.source)
        return float(value.value)

    @staticmethod
    def word(node: CommandSyntax, index: int) -> str:
        value = node.arguments[index]
        if not isinstance(value.value, str) or not value.value.strip():
            raise _ConversionError(f"{node.instruction} argument {index + 1} must be a nonempty string", value.source)
        return value.value

    def headers(self) -> int:
        offset = 0
        seen: set[str] = set()
        for header in self.ast.headers:
            canonical_name = "TimingPointDensityFactor" if header.name == "TimingPointsDensityFactor" else header.name
            if canonical_name in seen:
                self.diagnostics.append(Diagnostic("duplicate_header", f"Duplicate header {header.name}", source=header.source))
                continue
            seen.add(canonical_name)
            if header.name == "AudioOffset":
                self.features.add("audio_offset")
                if not _INTEGER.fullmatch(header.value):
                    self.diagnostics.append(Diagnostic("invalid_header", "AudioOffset must be an integer number of milliseconds", source=header.source))
                else:
                    try:
                        offset = int(header.value)
                    except ValueError:
                        self.diagnostics.append(Diagnostic("invalid_header", "AudioOffset exceeds the parser's supported integer range", source=header.source))
                        continue
                    if not -(2**31) <= offset <= 2**31 - 1:
                        self.diagnostics.append(Diagnostic("invalid_header", "AudioOffset exceeds the signed 32-bit integer range", source=header.source))
            elif canonical_name == "TimingPointDensityFactor":
                self.features.add("timing_point_density")
                try:
                    density = Decimal(header.value)
                    if not density.is_finite() or density <= 0 or not math.isfinite(float(density)):
                        raise InvalidOperation
                except InvalidOperation:
                    self.diagnostics.append(Diagnostic("invalid_header", f"{header.name} must be finite and positive", source=header.source))
                    continue
                if density != 1:
                    self.block("unsupported_timing_density", "Non-default timing-point density is retained in the AST; the current ChartIR cannot represent its judgement semantics", header)
            else:
                self.features.add("unknown_header")
                self.block("unknown_header", f"Unknown header {header.name!r} is retained; its input semantics are unknown", header)
        return offset

    def convert(self, nodes: tuple[CommandSyntax, ...], group_id: str = "default", nested: bool = False) -> None:
        for node in nodes:
            event_id = self.event_id(node, group_id)
            try:
                if isinstance(node, TimingGroupSyntax):
                    attrs = [part for index in range(len(node.arguments)) for part in self.word(node, index).split("_") if part]
                    child_id = self.identifier("timinggroup", (group_id, attrs))
                    # noinput: the group's notes are drawn but not judged.
                    # fadingholds only changes how the group's holds fade;
                    # input and judgement are unchanged.
                    known = bool(attrs) and set(attrs) <= {"noinput", "fadingholds"}
                    noinput = known and "noinput" in attrs
                    unsupported = (bool(attrs) and not known) or nested
                    self.groups.append(TimingGroup(group_id=child_id, parent_group_id=group_id, raw_attributes=attrs, semantics_status="unsupported" if unsupported else "supported"))
                    self.features.add("timinggroup_attributes" if attrs else "timinggroup_default")
                    self.features.add("timinggroup_independent")
                    if noinput:
                        self.features.add("timinggroup_noinput")
                    if known and "fadingholds" in attrs:
                        self.features.add("timinggroup_fadingholds")
                        if not any(d.code == "timinggroup_fadingholds_visual" for d in self.diagnostics):
                            self.diagnostics.append(Diagnostic("timinggroup_fadingholds_visual",
                                "fadingholds is retained as a visual hold-fade switch. Input and judgement are "
                                "unchanged.", "info", node.source, None, False))
                    if attrs and not known:
                        self.block("unsupported_timinggroup_attributes", f"Timinggroup attributes {attrs!r} are retained but their input semantics are not implemented", node)
                    if nested:
                        self.features.add("timinggroup_nested")
                        self.block("unsupported_nested_timinggroup", "Nested timinggroups are parsed, but nesting has no defined meaning", node)
                    self.convert(node.children, child_id, nested=True)
                    continue
                base = dict(event_id=event_id, source=node.source, timinggroup_id=group_id)
                if isinstance(node, TapSyntax):
                    self.count(node, {2})
                    time, lane = self.integer(node, 0), self.number(node, 1)
                    if lane not in {1, 2, 3, 4}:
                        self.raw(node, base, time, time, "unsupported_ground_lane", f"Ground lane {lane} is valid AFF syntax, but this model supports only integer lanes 1 through 4", "ground_lane_extended")
                    else:
                        self.events.append(TapEvent(**base, type="tap", start_time_ms=time, end_time_ms=time, lane=int(lane)))
                    self.features.add("tap")
                elif isinstance(node, HoldSyntax):
                    self.count(node, {3})
                    start, end, lane = self.integer(node, 0), self.integer(node, 1), self.number(node, 2)
                    if end <= start:
                        raise _ConversionError("hold requires positive duration", node.arguments[1].source)
                    if lane not in {1, 2, 3, 4}:
                        self.raw(node, base, start, end, "unsupported_ground_lane", f"Ground lane {lane} is valid AFF syntax, but this model supports only integer lanes 1 through 4", "ground_lane_extended")
                    else:
                        self.events.append(HoldEvent(**base, type="hold", start_time_ms=start, end_time_ms=end, lane=int(lane)))
                    self.features.add("hold")
                elif isinstance(node, TimingSyntax):
                    self.count(node, {3})
                    time, bpm, divisor = self.integer(node, 0), self.number(node, 1), self.number(node, 2)
                    if divisor < 0:
                        raise _ConversionError("timing beats-per-line divisor cannot be negative", node.arguments[2].source)
                    if divisor == 0:
                        self.raw(node, base, time, time, "unsupported_timing_divisor", "Zero timing divisor is valid AFF syntax, but the current ChartIR requires positive beats_per_line", "timing_divisor_zero")
                    else:
                        self.events.append(TimingEvent(**base, type="timing", start_time_ms=time, end_time_ms=time, bpm=bpm, beats_per_line=divisor))
                    self.features.add("timing")
                elif isinstance(node, ArcSyntax):
                    self.arc(node, base)
                elif isinstance(node, ArcTapSyntax):
                    raise _ConversionError("arctap must occur inside its parent Arc's child list", node.source)
                else:
                    time, end = 0, 0
                    if isinstance(node, CameraSyntax):
                        self.count(node, {9})
                        time = self.integer(node, 0)
                        for index in range(1, 7):
                            self.number(node, index)
                        self.word(node, 7)
                        duration = self.integer(node, 8)
                        if duration < 0:
                            raise _ConversionError("camera duration cannot be negative", node.arguments[8].source)
                        end = time + duration
                    elif isinstance(node, SceneControlSyntax):
                        if len(node.arguments) < 2:
                            raise _ConversionError("scenecontrol requires a time and type", node.source)
                        time = end = self.integer(node, 0)
                        scene_name = self.word(node, 1)
                        for argument in node.arguments[2:]:
                            if argument.kind == "identifier":
                                raise _ConversionError("scenecontrol data arguments must be numbers or quoted strings", argument.source)
                            if argument.kind == "number" and not math.isfinite(float(argument.value)):
                                raise _ConversionError("scenecontrol arguments must be finite", argument.source)
                        if scene_name in {"arcahvdistort", "arcahvdebris", "redline"}:
                            self.count(node, {4})
                            self.events.append(SceneControlEvent(**base, type="scenecontrol", name=scene_name,
                                arguments=[self.number(node, 2), self.number(node, 3)], raw_text=node.raw_text,
                                start_time_ms=time, end_time_ms=end))
                            self.features.add(f"scenecontrol_{scene_name}")
                            if not any(d.code == "scenecontrol_visual_only" for d in self.diagnostics):
                                self.diagnostics.append(Diagnostic("scenecontrol_visual_only",
                                    "arcahvdistort, arcahvdebris and redline are retained with their arguments; "
                                    "they change only visuals and do not affect input or judgement.",
                                    "info", node.source, event_id, False))
                            continue
                        if scene_name == "hidegroup":
                            # The command belongs to its timinggroup and toggles
                            # that group's visibility: argument 3 is unused,
                            # argument 4 is "hide" and stores 1-hide at the command
                            # time. It never changes judgement or contact.
                            self.count(node, {4})
                            self.events.append(SceneControlEvent(**base, type="scenecontrol", name="hidegroup",
                                arguments=[self.number(node, 2), self.number(node, 3)], raw_text=node.raw_text,
                                start_time_ms=time, end_time_ms=end))
                            self.features.add("scenecontrol_hidegroup")
                            if not any(d.code == "scenecontrol_hidegroup_scope" for d in self.diagnostics):
                                self.diagnostics.append(Diagnostic("scenecontrol_hidegroup_scope",
                                    "hidegroup is retained as a per-timinggroup visibility switch (the group "
                                    "is drawn with visibility 1-hide). Notes of the group remain judged; "
                                    "only their rendering is hidden.",
                                    "info", node.source, event_id, False))
                            continue
                    elif node.arguments and node.arguments[0].kind == "number" and _INTEGER.fullmatch(node.arguments[0].raw_text):
                        proposed_time = int(node.arguments[0].raw_text)
                        # Unknown instructions have no established time grammar;
                        # infer a display position only for representable integers.
                        if -(2**31) <= proposed_time <= 2**31 - 1:
                            time = end = proposed_time
                    feature = node.instruction if isinstance(node, (CameraSyntax, SceneControlSyntax)) else "unknown_instruction"
                    self.features.add(feature)
                    reason = f"{node.instruction} syntax is preserved; its effect on input and display is not implemented"
                    self.events.append(RawAFFEvent(**base, type="raw_unsupported", instruction=node.instruction, raw_text=node.raw_text, unsupported_reason=reason, start_time_ms=time, end_time_ms=end))
                    self.block(f"unsupported_{feature}", reason, node, event_id)
            except (_ConversionError, ValidationError, OverflowError) as error:
                message = error.message if isinstance(error, _ConversionError) else str(error)
                source = error.source if isinstance(error, _ConversionError) else node.source
                self.diagnostics.append(Diagnostic("invalid_aff_arguments", message, source=source, event_id=event_id))

    def raw(self, node: CommandSyntax, base: dict, start: int, end: int, code: str, reason: str, feature: str) -> None:
        self.features.add(feature)
        self.events.append(RawAFFEvent(**base, type="raw_unsupported", instruction=node.instruction, raw_text=node.raw_text, unsupported_reason=reason, start_time_ms=start, end_time_ms=end))
        self.block(code, reason, node, base["event_id"])

    def arc(self, node: ArcSyntax, base: dict) -> None:
        self.count(node, {10, 11})
        start, end = self.integer(node, 0), self.integer(node, 1)
        reversed_duration = end < start
        if reversed_duration:
            # Reversed arcs in charts are traces (no ticks): keep the event as
            # a zero-length visual segment.
            end = start
        curve, effect = self.word(node, 4), self.word(node, 8)
        trace = node.arguments[9]
        if not isinstance(trace.value, bool) or trace.kind != "identifier":
            raise _ConversionError("arc argument 10 must be true or false", trace.source)
        arc = ArcEvent(**base, type="arc", start_time_ms=start, end_time_ms=end,
                       start_aff_xy=[self.number(node, 2), self.number(node, 5)],
                       end_aff_xy=[self.number(node, 3), self.number(node, 6)],
                       interpolation=curve, color=self.integer(node, 7), effect=effect,
                       is_guideline=trace.value, connected_event_ids=[])
        self.events.append(arc)
        if reversed_duration:
            self.features.add("reversed_arc_duration")
            self.diagnostics.append(Diagnostic("reversed_arc_duration",
                "Arc end precedes its start; the zero-length visual segment is retained. "
                "A playable reversed arc is not supported.",
                "warning", node.source, arc.event_id, False))
        self.features.add("guideline" if arc.is_guideline else "arc_straight" if curve == "s" else "arc_curves")
        self.features.add(f"arc_curve_{curve}" if curve in SUPPORTED_CURVES else "unknown_curve")
        if curve not in SUPPORTED_CURVES:
            self.features.add("unknown_curve")
            self.block("unsupported_arc_curve", f"Unknown Arc interpolation {curve!r} is preserved", node, arc.event_id)
        if effect != "none":
            self.features.add("arc_effect")
            self.block("unsupported_arc_effect", f"Arc effect {effect!r} is preserved but its input semantics are not implemented", node, arc.event_id)
        if len(node.arguments) == 11:
            resolution = self.number(node, 10)
            if resolution <= 0:
                raise _ConversionError("arc resolution multiplier must be positive", node.arguments[10].source)
            self.features.add("arc_resolution")
            if resolution != 1:
                self.block("unsupported_arc_resolution", "Non-default Arc resolution is retained in the AST but is not represented in ChartIR", node, arc.event_id)
        for child in node.arctaps:
            child_id = self.identifier("arctap", (arc.event_id, tuple(_canonical_scalar(argument) for argument in child.arguments[:1])))
            try:
                self.count(child, {1, 2})
                time = self.integer(child, 0)
                if not start <= time <= end:
                    raise _ConversionError("arctap time lies outside its parent Arc", child.arguments[0].source)
                self.events.append(ArcTapEvent(event_id=child_id, source=child.source, type="arctap", start_time_ms=time, end_time_ms=time, timinggroup_id=arc.timinggroup_id, parent_arc_id=arc.event_id))
                self.features.add("arctap")
                if len(child.arguments) == 2:
                    width = self.number(child, 1)
                    if width <= 0:
                        raise _ConversionError("arctap width must be positive", child.arguments[1].source)
                    if width != 1:
                        self.features.add("arctap_width")
                        self.block("unsupported_arctap_width", "Non-default ArcTap width is retained in the AST but cannot be represented in ChartIR", child, child_id)
            except (_ConversionError, ValidationError, OverflowError) as error:
                message = error.message if isinstance(error, _ConversionError) else str(error)
                source = error.source if isinstance(error, _ConversionError) else child.source
                self.diagnostics.append(Diagnostic("invalid_aff_arguments", message, source=source, event_id=child_id))

    def connect_arcs(self) -> None:
        try:
            count = connect_arcs(self.events, self.groups)
        except (OverflowError, ValueError) as error:
            self.diagnostics.append(Diagnostic("invalid_arc_topology", str(error)))
            return
        if count:
            self.features.add("arc_connections")
            first = next(e for e in self.events if isinstance(e, ArcEvent) and e.connected_event_ids)
            self.diagnostics.append(Diagnostic("arc_connection_scope",
                "Arcs are linked where one ends at another's start, with the chart loader's float coordinates, "
                "load order and colour preference. Branches and colour changes do not grant touch sharing; "
                "planners reuse only reciprocal, temporally compatible links.", "info", first.source,
                first.event_id, False))


def parse_aff(text: str, source_file: str = "<memory>", chart_sha256: str | None = None) -> ParsedChart:
    """Parse AFF without silently dropping unknown or malformed syntax.

    Unsupported but syntactically valid features yield a preserved ChartIR and
    blocking warnings. Syntax or invalid supported arguments yield ``chart=None``.
    ``chart_sha256`` may be supplied by a byte-reading caller (e.g. BOM input).
    """
    diagnostics: list[Diagnostic] = []
    ast = AFFSyntax(source_file=source_file)
    try:
        tokens = _lex(text)
        parser = _Parser(text, source_file, tokens, diagnostics)
        ast = parser.document()
    except _SyntaxError as error:
        source = SourceLocation(file=source_file, line=error.token.line, column=error.token.column)
        diagnostics.append(Diagnostic("aff_syntax_error", error.message, source=source))
    converter = _Converter(ast, diagnostics)
    offset = converter.headers()
    converter.features.add("timinggroup_default")
    converter.convert(ast.statements)
    converter.connect_arcs()
    chart = None
    if not any(item.severity == "error" for item in diagnostics):
        try:
            chart = ChartIR(schema_version="1.0", chart_sha256=chart_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(), audio_offset_ms=offset, source_time_unit="ms", coordinate_unit="aff", timinggroups=converter.groups, events=converter.events)
        except ValidationError as error:
            diagnostics.append(Diagnostic("invalid_chart_ir", str(error)))
    return ParsedChart(chart, diagnostics, ast, sorted(converter.features))
