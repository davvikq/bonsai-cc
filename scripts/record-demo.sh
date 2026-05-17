#!/usr/bin/env bash
# Record the README demo GIF.
#
# Uses vhs (https://github.com/charmbracelet/vhs) to drive a real
# `bonsai-cc watch --replay` against the bundled mixed-tools
# fixture. Outputs `docs/demo.gif`.
#
# Usage:
#     scripts/record-demo.sh
#
# Requirements:
#     - vhs in PATH (`go install github.com/charmbracelet/vhs@latest`)
#     - bonsai-cc available as `bonsai-cc` on PATH (`uv tool install .`
#       in this repo before running)

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v vhs >/dev/null; then
    echo "vhs not found. Install it from https://github.com/charmbracelet/vhs" >&2
    exit 1
fi

if ! command -v bonsai-cc >/dev/null; then
    echo "bonsai-cc not on PATH. Run 'uv tool install .' first." >&2
    exit 1
fi

mkdir -p docs

cat > /tmp/bonsai-demo.tape <<'TAPE'
Output docs/demo.gif

Set FontSize 16
Set Width 1200
Set Height 720
Set Theme "Dracula"
Set TypingSpeed 60ms

Type "bonsai-cc watch --replay tests/fixtures/events/mixed_tools.jsonl --speed 6"
Enter
Sleep 8s
Type "q"
Sleep 500ms
TAPE

vhs /tmp/bonsai-demo.tape
rm -f /tmp/bonsai-demo.tape
echo "Wrote docs/demo.gif"
