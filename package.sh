#!/usr/bin/env bash
set -euo pipefail

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg="$dir/.build/package"

rm -rf "$dir/.build"
mkdir -p "$pkg"
pip3 install --quiet --target "$pkg" -r "$dir/requirements.txt"
cp "$dir/handler.py" "$pkg/"
(cd "$pkg" && zip -qr "$dir/.build/lambda.zip" .)
