#!/usr/bin/env bash
# Benchforge をローカル(config.yaml の host/port)で起動。
set -uo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python run.py
