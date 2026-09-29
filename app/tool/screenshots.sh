#!/usr/bin/env bash
# Rewrite the README's screenshots.
#
#     cd app && tool/screenshots.sh
#
# One `flutter test` per shot, and the PNG is the result rather than the exit
# code. Two things about the map force both, and both were established the
# slow way:
#
#   * it drives itself off a post-frame callback that reschedules itself every
#     frame, so once a map has been mounted the test binding never reaches
#     idle — it will not end the test and it will not accept a second
#     `pumpWidget`. Every shot rendered correctly and then sat there until the
#     ten-minute timeout killed it.
#   * so each run calls `exit()` the moment it has its file, which kills the
#     isolate, which the harness reports as a failure. The file on disk is the
#     honest answer.
set -uo pipefail
cd "$(dirname "$0")/.."
out=../docs/img
mkdir -p "$out"
fail=0
for shot in world record castle; do
  printf '%-8s ' "$shot"
  rm -f "$out/$shot.png"
  flutter test tool/screenshots.dart --plain-name "$shot" \
      >"/tmp/shot-$shot.log" 2>&1
  if [ -s "$out/$shot.png" ]; then
    echo "ok   $(du -h "$out/$shot.png" | cut -f1)"
  else
    echo 'FAILED'
    tail -25 "/tmp/shot-$shot.log"
    fail=1
  fi
done
exit $fail
