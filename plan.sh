#!/bin/sh
set -eu
cd "$(dirname "$0")"
chart=${1:-charts/desk-study.aff}
output=${2:-output/desk-study.plan.json}
mkdir -p "$(dirname "$output")"
exec .venv/bin/python -m arc_hand_order plan "$chart" --preset on5 \
    --set second_finger=false --set second_finger_again=false --set full_arc_hold=false --out "$output"
