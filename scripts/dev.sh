#!/bin/sh
# API and the Vite UI together. Ctrl+C stops both.
set -eu
cd "$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"

yarn start &
api=$!
yarn dev:web &
web=$!

trap 'kill "$api" "$web" 2>/dev/null || true; wait "$api" "$web" 2>/dev/null || true' INT TERM EXIT
wait "$web"
