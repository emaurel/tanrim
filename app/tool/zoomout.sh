#!/usr/bin/env bash
# Rewrite the README's zoom-out gif.
#
#     cd app && tool/zoomout.sh
#
# Two steps, because Flutter can render a frame and cannot write a gif, and
# Pillow can write a gif and cannot render a frame. The first is the same
# harness as the still screenshots — same fixtures, same widgets — so the gif
# cannot drift from them.
set -uo pipefail
cd "$(dirname "$0")/.."
frames=../docs/img/zoomout

flutter test tool/screenshots.dart --plain-name zoomout >/tmp/zoomout.log 2>&1
n=$(ls "$frames"/*.png 2>/dev/null | wc -l)
if [ "$n" -lt 2 ]; then
  echo 'FAILED to render frames'; tail -25 /tmp/zoomout.log; exit 1
fi
echo "rendered $n frames"

../.venv/bin/python ../tools/make_gif.py "$frames" ../docs/img/zoomout.gif
