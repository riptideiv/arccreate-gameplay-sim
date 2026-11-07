"""Translate designant traces for the installed ArcCreate export adapter only.

Original AFF files stay unchanged. The private SFX tag lets the native adapter
recognize non-scoring red sky taps while keeping ordinary touch judgement.
"""
import json
import os
from pathlib import Path

from arc_hand_order.chart.parser import parse_aff

MARKER = "__rhythm_red_note__"


def render_chart(text, enabled=True):
    parsed = parse_aff(text)
    if parsed.chart is None:
        raise ValueError("Cannot prepare a chart with invalid AFF syntax")
    offsets, at = [], 0
    for line in text.splitlines(keepends=True):
        offsets.append(at)
        at += len(line)
    replacements = []
    def visit(nodes):
        for node in nodes:
            if node.instruction == "timinggroup":
                visit(node.children)
            elif node.instruction == "arc" and node.arguments[9].value == "designant":
                start = offsets[node.source.line - 1] + node.source.column - 1
                replacement = node.raw_text
                if enabled:
                    end = replacement.index(')')
                    arguments = replacement[replacement.index('(')+1:end].split(',')
                    arguments[8], arguments[9] = MARKER, 'true'
                    replacement = 'arc(' + ','.join(arguments) + replacement[end:]
                else:
                    # Preserve line numbers for every remaining source command.
                    replacement = ''.join('\n' if c == '\n' else ' ' for c in replacement)
                replacements.append((start, start+len(node.raw_text), replacement))
    visit(parsed.ast.statements)
    for start, end, replacement in reversed(sorted(replacements)):
        text = text[:start] + replacement + text[end:]
    return text, len(replacements)


def prepare_project(chart, aff, project, run, enabled=True):
    text = aff.read_text(encoding='utf-8-sig')
    converted, count = render_chart(text, enabled)
    if not count:
        return chart, aff, project
    folder = run / 'arccreate'
    folder.mkdir(exist_ok=True)
    for asset in chart.iterdir():
        if asset.name in (aff.name, 'project.arcproj', '.rendering') or not asset.is_file():
            continue
        target = folder / asset.name
        if not target.exists() and not target.is_symlink():
            target.symlink_to(os.path.relpath(asset, folder))
    (folder / aff.name).write_text(converted)
    import re
    source = project.read_text()
    source = re.sub(r'(?m)^\s*lastOpenedChartPath:.*\n', '', source)
    (folder / 'project.arcproj').write_text('lastOpenedChartPath: ' + json.dumps(aff.name) + '\n' + source)
    return folder, folder / aff.name, folder / 'project.arcproj'
