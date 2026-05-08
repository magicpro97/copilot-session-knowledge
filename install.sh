#!/bin/sh
# install.sh — Install sk binary from GitHub Releases
# Usage: curl -fsSL https://raw.githubusercontent.com/magicpro97/copilot-session-knowledge/main/install.sh | sh
set -eu

REPO="magicpro97/copilot-session-knowledge"
BINARY="sk"

# --- OS and architecture detection ---

detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "darwin" ;;
        *)       echo "unsupported" ;;
    esac
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)  echo "x64" ;;
        aarch64|arm64) echo "arm64" ;;
        *)             echo "unsupported" ;;
    esac
}

OS=$(detect_os)
ARCH=$(detect_arch)

if [ "$OS" = "unsupported" ]; then
    echo "Error: Unsupported operating system: $(uname -s)" >&2
    echo "Supported: Linux, macOS" >&2
    exit 1
fi

if [ "$ARCH" = "unsupported" ]; then
    echo "Error: Unsupported architecture: $(uname -m)" >&2
    echo "Supported: x86_64/amd64, aarch64/arm64" >&2
    exit 1
fi

# --- Version detection ---

if [ -n "${SK_VERSION:-}" ]; then
    VERSION="$SK_VERSION"
else
    VERSION=$(curl -sSf "https://api.github.com/repos/$REPO/releases/latest" | grep '"tag_name"' | cut -d'"' -f4)
    if [ -z "$VERSION" ]; then
        echo "Error: Could not determine latest version" >&2
        exit 1
    fi
fi

# --- Download ---

ASSET="${BINARY}-${OS}-${ARCH}.tar.gz"
URL="https://github.com/$REPO/releases/download/$VERSION/$ASSET"
CHECKSUM_URL="${URL}.sha256"

INSTALL_DIR="${SK_INSTALL_DIR:-$HOME/.copilot/bin}"
mkdir -p "$INSTALL_DIR"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Installing sk $VERSION ($OS/$ARCH)..."
echo "  Downloading: $URL"

curl -sSfL "$URL" -o "$TMP_DIR/$ASSET"

# --- Checksum verification ---

if curl -sSfL "$CHECKSUM_URL" -o "$TMP_DIR/$ASSET.sha256" 2>/dev/null; then
    EXPECTED=$(awk '{print $1}' "$TMP_DIR/$ASSET.sha256")
    if command -v shasum >/dev/null 2>&1; then
        ACTUAL=$(shasum -a 256 "$TMP_DIR/$ASSET" | awk '{print $1}')
    elif command -v sha256sum >/dev/null 2>&1; then
        ACTUAL=$(sha256sum "$TMP_DIR/$ASSET" | awk '{print $1}')
    else
        ACTUAL=""
        echo "  Warning: No SHA-256 tool found, skipping verification"
    fi

    if [ -n "$ACTUAL" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
        echo "Error: Checksum mismatch!" >&2
        echo "  Expected: $EXPECTED" >&2
        echo "  Got:      $ACTUAL" >&2
        exit 1
    fi
    echo "  Checksum verified ✓"
else
    echo "  Warning: Checksum file not available, skipping verification"
fi

# --- Extract and install ---

tar xzf "$TMP_DIR/$ASSET" -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/$BINARY"

echo "  Installed: $INSTALL_DIR/$BINARY"
echo ""

# --- PATH setup ---

case ":${PATH}:" in
    *":$INSTALL_DIR:"*)
        echo "✅ sk is ready! Run: sk --version"
        ;;
    *)
        echo "⚠️  Add to your PATH:"
        echo ""
        SHELL_NAME=$(basename "${SHELL:-/bin/sh}")
        case "$SHELL_NAME" in
            zsh)
                echo "  echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.zshrc"
                echo "  source ~/.zshrc"
                ;;
            bash)
                echo "  echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.bashrc"
                echo "  source ~/.bashrc"
                ;;
            fish)
                echo "  fish_add_path $INSTALL_DIR"
                ;;
            *)
                echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
                ;;
        esac
        echo ""
        echo "Then run: sk --version"
        ;;
esac
