#!/bin/sh
set -eu
cd "$(dirname "$0")"
blender=${BLENDER_BIN:-$(command -v blender || true)}
blender=${blender:-.tools/Blender.app/Contents/MacOS/Blender}
blend=${1:?provide the baked Blender file}
gameplay=${2:?provide the ArcCreate clip used in Blender}
video=${3:?provide the output MP4 path}
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ipad-rhythm.XXXXXXXX")
trap 'rm -rf "$temp_dir"' EXIT
# Blender's movie texture advances one source frame per scene frame.
# Give the 30 fps scene a 30 fps movie, while retaining the original audio.
ffmpeg -hide_banner -loglevel error -i "$gameplay" -map 0:v:0 -an \
    -vf fps=30:start_time=0:round=up -c:v libx264 -crf 16 -pix_fmt yuv420p "$temp_dir/screen.mp4"
ARCCREATE_SCREEN_MOVIE="$temp_dir/screen.mp4" "$blender" --factory-startup -b "$blend" \
    --python-exit-code 1 --python-expr '
import bpy
import os
nodes = bpy.data.materials["Tablet | ArcCreate gameplay"].node_tree.nodes
nodes["Image Texture"].image = bpy.data.images.load(os.environ["ARCCREATE_SCREEN_MOVIE"])
' -o "$temp_dir/frame-" -a
ffmpeg -hide_banner -loglevel error -framerate 30 -i "$temp_dir/frame-%04d.png" \
    -i "$gameplay" -map 0:v:0 -map 1:a:0 -c:v libx264 -crf 19 -pix_fmt yuv420p \
    -c:a aac -movflags +faststart -shortest "$temp_dir/preview.mp4"
mv "$temp_dir/preview.mp4" "$video"
