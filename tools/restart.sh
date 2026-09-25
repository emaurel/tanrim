#!/usr/bin/env bash
# Restart the backend, killing EVERY instance rather than whichever holds the
# port.
#
# `kill <the-listener>` leaves the others running, and a uvicorn that has lost
# the port keeps its orchestrator loop going — still polling mail, still
# sweeping leads, still dispatching, still writing to state/. Six accumulated
# over one afternoon and produced 1000 log events an hour between them.
#
# Matching is on the python interpreter's own argv, and never with a bare
# `pkill -f`: that pattern matches any shell whose command line merely CONTAINS
# it, which on the first attempt killed the terminal running this script.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${TANRIM_PORT:-8765}"
SELF=$$

instances() {
    pgrep -a python 2>/dev/null \
        | awk '/uvicorn/ && /tanrim\.server:app/ {print $1}' \
        | grep -v "^${SELF}$" || true
}

for sig in TERM KILL; do
    pids=$(instances)
    [ -z "$pids" ] && break
    echo "  killing ($sig): $(echo "$pids" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill -"$sig" $pids 2>/dev/null || true
    sleep 2
done

PYTHONPATH=backend nohup .venv/bin/python -m uvicorn tanrim.server:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning \
    >> state/server.log 2>&1 &
disown

for _ in $(seq 30); do
    if curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null; then
        echo "up: $(curl -s --max-time 5 "http://127.0.0.1:$PORT/health")"
        echo "instances: $(instances | wc -l)"
        exit 0
    fi
    sleep 1
done
echo "did not come up; see state/server.log" >&2
exit 1
