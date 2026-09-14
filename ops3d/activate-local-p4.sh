#!/bin/bash
set -euo pipefail
base=/srv/canvaslab-3d
id canvaslab3d >/dev/null 2>&1 || useradd --system --user-group --home-dir "$base" --shell /usr/sbin/nologin canvaslab3d
for group in video render; do
  if getent group "$group" >/dev/null; then usermod -aG "$group" canvaslab3d; fi
done
mkdir -p "$base/data"
chown -R canvaslab3d:canvaslab3d "$base"
chmod 700 "$base/data"
test ! -f "$base/data/access.token" || chmod 600 "$base/data/access.token"
install -m 644 "$base/app/ops3d/canvaslab3d.service" /etc/systemd/system/canvaslab3d.service
install -m 644 "$base/app/ops3d/canvaslab3d-gpu@.service" /etc/systemd/system/canvaslab3d-gpu@.service
systemctl daemon-reload
systemctl enable --now canvaslab3d.service canvaslab3d-gpu@0.service canvaslab3d-gpu@1.service
