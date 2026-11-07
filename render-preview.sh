#!/bin/sh
set -eu
cd "$(dirname "$0")"
blender=${BLENDER_BIN:-../../work/tools/Blender.app/Contents/MacOS/Blender}
blend=${1:?provide the baked Blender file}
gameplay=${2:?provide the ArcCreate clip used in Blender}
video=${3:?provide the output MP4 path}
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ipad-rhythm.XXXXXXXX")
trap 'rm -rf "$temp_dir"' EXIT
"$blender" --factory-startup -b "$blend" -o "$temp_dir/frame-" -a
ffmpeg -hide_banner -loglevel error -framerate 30 -i "$temp_dir/frame-%04d.png" \
    -i "$gameplay" -map 0:v:0 -map 1:a:0 -c:v libx264 -crf 19 -pix_fmt yuv420p \
    -c:a aac -movflags +faststart -shortest "$temp_dir/preview.mp4"
mv "$temp_dir/preview.mp4" "$video"
