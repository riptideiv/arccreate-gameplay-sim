#!/bin/sh
set -eu
if [ "$#" -ne 4 ] && [ "$#" -ne 5 ]; then
    echo "usage: $0 project.arcproj output.mp4 start_ms end_ms [touch-replay.json]" >&2
    exit 2
fi
adapter_dir=$(cd "$(dirname "$0")" && pwd)
app="${ARCCREATE_EXPORT_APP:-$adapter_dir/../.tools/ArcCreate/ArcCreate-media.app}/Contents/MacOS/ArcCreate"
project="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
mkdir -p "$(dirname "$2")"
output="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
start=$3
end=$4
replay=${5:-}
temp_dir=$(mktemp -d "$(dirname "$output")/.arccreate.XXXXXXXX")
trap 'rm -rf "$temp_dir"' EXIT
export_gameplay() {
    "$app" "$project" --export-aff-video "$temp_dir/gameplay.mp4" "$start" "$end" "$(command -v ffmpeg)" "$@" \
        -screen-fullscreen 0 -screen-width 1148 -screen-height 802 -logFile "$temp_dir/export.log"
}
if [ -n "$replay" ]; then
    replay="$(cd "$(dirname "$replay")" && pwd)/$(basename "$replay")"
    export_gameplay --touch-replay "$replay" || { cp "$temp_dir/export.log" "$output.export.log"; tail -80 "$temp_dir/export.log" >&2; exit 1; }
    rate=60
else
    export_gameplay || {
    tail -80 "$temp_dir/export.log" >&2
    exit 1
}
    rate=30
fi
cp "$temp_dir/export.log" "$output.export.log"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate \
    -of csv=p=0 "$temp_dir/gameplay.mp4" | grep -qx "1148,802,$rate/1"
if [ -n "$replay" ]; then
    ffmpeg -hide_banner -loglevel error -i "$temp_dir/gameplay.mp4" \
        -map 0:v -an -r 30 -c:v libx264 -preset veryfast -crf 17 -pix_fmt yuv420p -movflags +faststart -y "$output.base.mp4"
    mv "$temp_dir/gameplay.mp4.music.wav" "$output.music.wav"
    mv "$temp_dir/gameplay.mp4.hits.wav" "$output.hits.wav"
    mv "$temp_dir/gameplay.mp4.sounds.json" "$output.sounds.json"
    mv "$temp_dir/gameplay.mp4.judgements.json" "$output.judgements.json"
    if [ -f "$temp_dir/gameplay.mp4.diagnostics.json" ]; then
        mv "$temp_dir/gameplay.mp4.diagnostics.json" "$output.diagnostics.json"
    fi
    "$adapter_dir/../.venv/bin/python" "$adapter_dir/../dashboard/media.py" "$output" "$output"
else
    mv "$temp_dir/gameplay.mp4" "$output"
fi
