#!/bin/bash
set -euo pipefail
base=/srv/canvaslab-3d
test -d "$base/app/server3d"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv nodejs npm curl xz-utils libxrender1 libxi6 libxxf86vm1 libxfixes3 libxkbcommon0 libgl1 fonts-noto-cjk
python3 -m venv "$base/venv"
"$base/venv/bin/pip" install -r "$base/app/server3d/requirements.txt"
python3 -m venv "$base/gpu-venv"
"$base/gpu-venv/bin/pip" install -r "$base/app/gpu3d/requirements-p4.txt"
cd "$base/app"
"$base/gpu-venv/bin/python" -m gpu3d.models download --models "$base/models"
npm --prefix runtime3d ci
npm --prefix runtime3d run build
cd runtime3d
npx playwright-core install chrome
mkdir -p "$base/tools"
cd "$base/tools"
archive=blender-4.2.0-linux-x64.tar.xz
url=https://download.blender.org/release/Blender4.2
if ! test -x blender-4.2.0-linux-x64/blender; then
  curl -fL --retry 3 --max-time 600 "$url/$archive" -o "$archive"
  curl -fL --retry 3 "$url/blender-4.2.0.sha256" -o checksums.txt
  grep -E '^[0-9a-fA-F]{64} [ *]blender-4\.2\.0-linux-x64\.tar\.xz$' checksums.txt > selected.sha256
  sha256sum --check selected.sha256
  tar -xJf "$archive"
fi
"$base/tools/blender-4.2.0-linux-x64/blender" --version
cd "$base/app"
CANVASLAB3D_GPU_ENABLED=1 "$base/gpu-venv/bin/python" -m gpu3d.worker --data "$base/data" --models "$base/models" --device cuda:0 --operations depth --check
CANVASLAB3D_GPU_ENABLED=1 "$base/gpu-venv/bin/python" -m gpu3d.worker --data "$base/data" --models "$base/models" --device cuda:1 --operations segment --check
echo SETUP_COMPLETE
