#!/usr/bin/env bash
# install.sh — Download and install the sk binary for copilot-session-knowledge
set -euo pipefail

REPO="magicpro97/copilot-session-knowledge"
INSTALL_DIR="${HOME}/.copilot/bin"
BINARY_NAME="sk"

# Detect OS and architecture
OS="$(uname -s)"
ARCH="$(uname -m)"

case "${OS}" in
  Linux)
    case "${ARCH}" in
      x86_64)  ASSET="sk-linux-x64.tar.gz" ;;
      aarch64) ASSET="sk-linux-arm64.tar.gz" ;;
      arm64)   ASSET="sk-linux-arm64.tar.gz" ;;
      *)
        echo "Unsupported architecture: ${ARCH}" >&2
        exit 1
        ;;
    esac
    ;;
  Darwin)
    case "${ARCH}" in
      x86_64) ASSET="sk-darwin-x64.tar.gz" ;;
      arm64)  ASSET="sk-darwin-arm64.tar.gz" ;;
      *)
        echo "Unsupported architecture: ${ARCH}" >&2
        exit 1
        ;;
    esac
    ;;
  *)
    echo "Unsupported OS: ${OS}. Use install.ps1 on Windows." >&2
    exit 1
    ;;
esac

echo "Detected: ${OS} ${ARCH} → asset: ${ASSET}"

# Resolve latest release tag
echo "Fetching latest release info from GitHub..."
LATEST_TAG=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
  | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\(.*\)".*/\1/')

if [ -z "${LATEST_TAG}" ]; then
  echo "Failed to determine latest release tag." >&2
  exit 1
fi

echo "Latest release: ${LATEST_TAG}"

DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${LATEST_TAG}/${ASSET}"
CHECKSUM_URL="${DOWNLOAD_URL}.sha256"

# Create install directory
mkdir -p "${INSTALL_DIR}"

ARCHIVE="${BINARY_NAME}-download.tar.gz"

echo "Downloading ${DOWNLOAD_URL} ..."
curl -fsSL -o "${ARCHIVE}" "${DOWNLOAD_URL}"

# Verify checksum
echo "Verifying checksum..."
EXPECTED=$(curl -fsSL "${CHECKSUM_URL}" | awk '{print $1}')
ACTUAL=$(sha256sum "${ARCHIVE}" | awk '{print $1}')
if [ "${EXPECTED}" != "${ACTUAL}" ]; then
  echo "Checksum mismatch! expected=${EXPECTED} got=${ACTUAL}" >&2
  rm -f "${ARCHIVE}"
  exit 1
fi

# Extract binary from archive
tar xzf "${ARCHIVE}" "${BINARY_NAME}"
rm -f "${ARCHIVE}"

DEST="${INSTALL_DIR}/${BINARY_NAME}"
mv "${BINARY_NAME}" "${DEST}"
chmod +x "${DEST}"

echo ""
echo "✅ sk ${LATEST_TAG} installed to ${DEST}"
echo ""

# PATH guidance
if echo ":${PATH}:" | grep -q ":${INSTALL_DIR}:"; then
  echo "✓ ${INSTALL_DIR} is already in your PATH."
else
  echo "⚠️  Add the following line to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
  echo ""
  echo "    export PATH=\"\${HOME}/.copilot/bin:\${PATH}\""
  echo ""
  echo "Then restart your shell or run: source ~/.bashrc"
fi
