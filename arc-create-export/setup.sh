#!/bin/sh
set -eu
cd "$(dirname "$0")"
source_app=../.tools/ArcCreate/build/StandaloneOSX/ArcCreate.app
export_app=${ARCCREATE_EXPORT_APP:-../.tools/ArcCreate/ArcCreate-red-notes.app}
cecil=${MONO_CECIL_DLL:-$(find -L /Library/Frameworks/Mono.framework/Versions/Current/lib/mono/gac/Mono.Cecil -path '*/0.11*/*dll' -print -quit)}
if [ ! -d "$source_app" ] || [ ! -f "$cecil" ]; then
    echo "ArcCreate 1.2.68 and Mono.Cecil 0.11 are required. See the prototype README." >&2
    exit 1
fi
if [ ! -d "$export_app" ]; then
    ditto "$source_app" "$export_app"
fi
managed="$export_app/Contents/Resources/Data/Managed"
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/arccreate-export.XXXXXXXX")
trap 'rm -rf "$temp_dir"' EXIT
cp "$source_app/Contents/Resources/Data/Managed/ArcCreate.Compose.dll" "$temp_dir"
cp "$source_app/Contents/Resources/Data/Managed/ArcCreate.Gameplay.dll" "$temp_dir"
mcs -target:library -out:"$temp_dir/ArcCreateOfflineExport.dll" -lib:"$managed" \
    -r:Newtonsoft.Json,netstandard,SceneTransition,ArcCreate.Compose,ArcCreate.Settings,ArcCreate.Gameplay,ArcCreate.Utility,ArcCreate.Data,UniTask,UnityEngine.CoreModule,UnityEngine.AudioModule,UnityEngine.JSONSerializeModule,UnityEngine.InputLegacyModule Export.cs Replay.cs RedNotes.cs
mcs -out:"$temp_dir/patch.exe" -r:"$cecil" Patch.cs
mono "$temp_dir/patch.exe" "$temp_dir"
cp "$temp_dir/ArcCreateOfflineExport.dll" "$temp_dir/ArcCreate.Compose.dll" "$temp_dir/ArcCreate.Gameplay.dll" "$managed"
