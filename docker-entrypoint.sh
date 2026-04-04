#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/media
mkdir -p /app/data
mkdir -p /app/data/maps
mkdir -p /app/data/crosswords
mkdir -p /app/dist

exec "$@"
