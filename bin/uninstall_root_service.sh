#!/usr/bin/env bash
set -euo pipefail

# Intentionally a no-op.
# We do not auto-remove system packages on plugin uninstall to avoid breaking
# other tools depending on pycups.

echo "No privileged uninstall actions required."
exit 0
