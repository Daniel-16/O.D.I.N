#!/bin/sh
# Convenience wrapper so ODIN can be run without activating the venv.
exec "$(dirname "$(readlink -f "$0")")/.venv/bin/python" -m odin "$@"
