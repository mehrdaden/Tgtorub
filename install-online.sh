
#!/usr/bin/env bash
set -Eeuo pipefail

URL="https://raw.githubusercontent.com/mehrdaden/Tgtorub/main/install.sh"

echo "Downloading Tgtorub installer..."

TMP_FILE="$(mktemp)"

if ! curl -fsSL "$URL" -o "$TMP_FILE"; then
    echo "ERROR: Could not download installer."
    rm -f "$TMP_FILE"
    exit 1
fi

if ! grep -q 'TGTORUB INSTALLER' "$TMP_FILE"; then
    echo "ERROR: Invalid installer file."
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 700 "$TMP_FILE"

sudo bash "$TMP_FILE"

rm -f "$TMP_FILE"
