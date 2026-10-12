#!/bin/bash
set -euo pipefail

CONTROLLER_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec /usr/bin/env python3 "$CONTROLLER_DIR/epoch_controller.py" "$@"
