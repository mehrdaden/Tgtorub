#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ZIP="https://github.com/mehrdaden/Tgtorub/archive/refs/heads/main.zip"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "Downloading Tgtorub project from GitHub..."
if ! curl -fsSL --retry 3 "$REPO_ZIP" -o "$TMP_DIR/repo.zip"; then
  echo "ERROR: Could not download the GitHub repository." >&2
  exit 1
fi

if ! command -v unzip >/dev/null 2>&1; then
  echo "ERROR: unzip is required. Install it with: sudo apt-get install unzip" >&2
  exit 1
fi

if ! unzip -q "$TMP_DIR/repo.zip" -d "$TMP_DIR"; then
  echo "ERROR: The GitHub ZIP archive is invalid." >&2
  exit 1
fi

PROJECT_DIR="$TMP_DIR/Tgtorub-main"
if [[ ! -f "$PROJECT_DIR/install.sh" ]]; then
  echo "ERROR: install.sh was not found in the downloaded repository." >&2
  exit 1
fi

if ! grep -q 'TGTORUB INSTALLER' "$PROJECT_DIR/install.sh"; then
  echo "ERROR: Installer marker check failed." >&2
  exit 1
fi

sudo bash "$PROJECT_DIR/install.sh"
