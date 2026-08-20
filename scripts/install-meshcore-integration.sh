#!/usr/bin/env bash
# =============================================================================
#  Install / update the MeshCore Home Assistant integration.
# =============================================================================
#  Fetches https://github.com/meshcore-dev/meshcore-ha and copies its
#  custom_components/meshcore into the Home Assistant config directory.
#
#  This is the supported integration — it speaks the MeshCore Companion
#  protocol over USB / BLE / TCP. Nothing in this stack reimplements it.
#
#    ./scripts/install-meshcore-integration.sh
#    REF=v1.2.3 ./scripts/install-meshcore-integration.sh   # pin a release
#
#  HACS is the alternative and gives you update notifications; this script
#  exists so the integration can be installed without HACS. Restart Home
#  Assistant afterwards, then add it from Settings > Devices & Services.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
STACK_DIR="$PWD"
TARGET="$STACK_DIR/homeassistant/config/custom_components"
REPO="${REPO:-https://github.com/meshcore-dev/meshcore-ha}"
REF="${REF:-main}"

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> fetching $REPO @ $REF"
git clone --depth 1 --branch "$REF" "$REPO" "$TMP/meshcore-ha" 2>&1 | sed 's/^/    /'

SRC="$TMP/meshcore-ha/custom_components/meshcore"
[ -d "$SRC" ] || { echo "custom_components/meshcore not found in the repository" >&2; exit 1; }

VERSION=$(grep -o '"version"[^,]*' "$SRC/manifest.json" 2>/dev/null | head -1 || echo "unknown")
COMMIT=$(git -C "$TMP/meshcore-ha" rev-parse --short HEAD)

mkdir -p "$TARGET"
rm -rf "$TARGET/meshcore"
cp -r "$SRC" "$TARGET/meshcore"

# Record exactly what was installed — `main` moves, and "it worked last month"
# is not a version.
cat > "$TARGET/meshcore/.installed-from" <<EOF
repo:      $REPO
ref:       $REF
commit:    $COMMIT
installed: $(date -Iseconds)
EOF

echo "==> installed meshcore integration ($VERSION, commit $COMMIT)"
echo "    -> $TARGET/meshcore"
echo
echo "Next:"
echo "  docker compose restart homeassistant"
echo "  then Settings > Devices & Services > + Add Integration > MeshCore"
echo "  see MESHCORE.md for connection choice and the telemetry mirror"
