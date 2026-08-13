#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

OUTPUT="${1:-scrobbler.pyz}"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "Installing dependencies into build dir..."
python3 -m pip install --target "$BUILD_DIR" -r requirements.txt --quiet

echo "Copying source files..."
cp start_standalone.py scrobble_utils.py date_detection.py ytmusic_fetcher.py "$BUILD_DIR/"
cp -r lastpy "$BUILD_DIR/"
find "$BUILD_DIR" -name '__pycache__' -type d -exec rm -rf {} +

echo "Building $OUTPUT..."
python3 -m zipapp "$BUILD_DIR" -m "start_standalone:main" -o "$OUTPUT" -p "/usr/bin/env python3"
chmod +x "$OUTPUT"

echo "Done: $OUTPUT"
