
#!/usr/bin/env bash
set -Eeuo pipefail

REPO="https://github.com/mehrdaden/Tgtorub.git"

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root"
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    apt-get update
    apt-get install -y git
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Downloading Tgtorub from GitHub..."

git clone --depth 1 "$REPO" "$WORKDIR"

cd "$WORKDIR"

if [[ ! -f install.sh ]]; then
    echo "ERROR: install.sh not found!"
    exit 1
fi

bash install.sh
