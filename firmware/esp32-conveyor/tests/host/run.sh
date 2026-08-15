#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIR="${TMPDIR:-/tmp}/asyl-esp32-host-tests"
mkdir -p "$BUILD_DIR"

cc -std=c11 -Wall -Wextra -Werror -pedantic \
  -I"$SCRIPT_DIR/../../main" \
  "$SCRIPT_DIR/../../main/command_guard.c" \
  "$SCRIPT_DIR/test_command_guard.c" \
  -o "$BUILD_DIR/test_command_guard"

"$BUILD_DIR/test_command_guard"
