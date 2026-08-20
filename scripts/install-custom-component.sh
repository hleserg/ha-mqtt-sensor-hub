#!/usr/bin/env bash
# =============================================================================
#  Install / update a Home Assistant custom component from a GitHub repository.
# =============================================================================
#  The generic form of install-meshcore-integration.sh, for the integrations
#  that have no place in this repository: they are somebody else's code under
#  somebody else's licence, and vendoring them would make this project claim
#  authorship it does not have.
#
#    ./scripts/install-custom-component.sh dext0r/yandex_smart_home
#    ./scripts/install-custom-component.sh AlexxIT/YandexStation
#    ./scripts/install-custom-component.sh owner/repo component_dir
#    REF=v1.2.3 ./scripts/install-custom-component.sh owner/repo
#
#  Without REF the latest *release tag* is used, not the default branch — a
#  default branch called `dev` is a real thing (dext0r/yandex_smart_home) and
#  installing from it silently is how a working stack acquires an unreleased
#  regression. If the repository publishes no releases, the default branch is
#  used and the script says so.
#
#  What gets installed is recorded in .installed-from inside the component, so
#  `main moved under us` is a diagnosable state rather than a mystery.
#
#  HACS is the alternative and adds update notifications. This exists so a
#  component can be installed without adding HACS itself as a dependency.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
STACK_DIR="$PWD"
TARGET="$STACK_DIR/homeassistant/config/custom_components"

usage() {
  echo "usage: $0 <owner/repo | git-url> [component_dir]" >&2
  echo "       REF=<tag|branch> $0 ...    # pin an exact ref" >&2
  exit 2
}

[ $# -ge 1 ] || usage
ARG="$1"
WANT="${2:-}"

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

# owner/repo -> https URL; anything containing :// or @ is taken as given.
if [[ "$ARG" == *"://"* || "$ARG" == *"@"* ]]; then
  REPO="$ARG"
  SLUG="$(basename "${ARG%.git}")"
else
  [[ "$ARG" == */* ]] || usage
  REPO="https://github.com/$ARG"
  SLUG="$ARG"
fi

# --- pick a ref -------------------------------------------------------------
REF="${REF:-}"
REF_KIND="pinned"
if [ -z "$REF" ] && [[ "$SLUG" == */* ]]; then
  REF="$(curl -fsSL "https://api.github.com/repos/$SLUG/releases/latest" 2>/dev/null \
         | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1 || true)"
  [ -n "$REF" ] && REF_KIND="latest release"
fi
if [ -z "$REF" ]; then
  REF_KIND="default branch"
  echo "==> no release found for $SLUG — falling back to the default branch"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CLONE="$TMP/src"

echo "==> fetching $REPO @ ${REF:-<default branch>} ($REF_KIND)"
if [ -n "$REF" ]; then
  git clone --depth 1 --branch "$REF" "$REPO" "$CLONE" 2>&1 | sed 's/^/    /'
else
  git clone --depth 1 "$REPO" "$CLONE" 2>&1 | sed 's/^/    /'
fi

CC="$CLONE/custom_components"
[ -d "$CC" ] || { echo "no custom_components/ directory in $REPO" >&2; exit 1; }

# --- pick the component -----------------------------------------------------
# Directories only: a repository may keep a stray __init__.py next to the real
# component (dext0r/yandex_smart_home does).
mapfile -t FOUND < <(find "$CC" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)

if [ -n "$WANT" ]; then
  NAME="$WANT"
  [ -d "$CC/$NAME" ] || { echo "custom_components/$NAME not found. Present: ${FOUND[*]}" >&2; exit 1; }
elif [ "${#FOUND[@]}" -eq 1 ]; then
  NAME="${FOUND[0]}"
else
  echo "several components in this repository — name the one you want:" >&2
  printf '    %s\n' "${FOUND[@]}" >&2
  exit 1
fi

SRC="$CC/$NAME"
[ -f "$SRC/manifest.json" ] || { echo "$NAME has no manifest.json — not a component" >&2; exit 1; }

VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SRC/manifest.json" | head -1)
MIN_HA=$(sed -n 's/.*"homeassistant"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SRC/manifest.json" | head -1)
COMMIT=$(git -C "$CLONE" rev-parse --short HEAD)

# --- install ----------------------------------------------------------------
mkdir -p "$TARGET"
if [ -d "$TARGET/$NAME" ]; then
  PREV=$(sed -n 's/^ref:[[:space:]]*//p' "$TARGET/$NAME/.installed-from" 2>/dev/null || true)
  echo "==> replacing existing $NAME${PREV:+ (was $PREV)}"
fi
rm -rf "$TARGET/$NAME"
cp -r "$SRC" "$TARGET/$NAME"

cat > "$TARGET/$NAME/.installed-from" <<EOF
repo:      $REPO
ref:       ${REF:-<default branch>}
ref_kind:  $REF_KIND
commit:    $COMMIT
version:   ${VERSION:-unknown}
installed: $(date -Iseconds)
EOF

echo "==> installed $NAME ${VERSION:+v$VERSION }(commit $COMMIT)"
echo "    -> $TARGET/$NAME"
[ -n "$MIN_HA" ] && echo "    requires Home Assistant >= $MIN_HA (this one runs $(cat "$STACK_DIR/homeassistant/config/.HA_VERSION" 2>/dev/null || echo '?'))"
echo
echo "Next:"
echo "  docker compose restart homeassistant"
echo "  then Settings > Devices & Services > + Add Integration"
echo
echo "Nothing is configured by installing it. A custom component that is never"
echo "added from the UI loads no code beyond its manifest and owns nothing."
