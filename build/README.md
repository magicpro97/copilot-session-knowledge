# Building sk Executable

## Prerequisites

| Platform | Python | C Compiler | Nuitka |
|----------|--------|-----------|--------|
| Windows | 3.12+ | MSVC (Visual Studio Build Tools) | `pip install nuitka` |
| Linux | 3.12+ | gcc or clang | `pip install nuitka` |
| macOS | 3.12+ | Xcode Command Line Tools | `pip install nuitka` |

## Quick Build

```bash
# Install Nuitka
pip install nuitka

# Build single-file executable
python build/nuitka-build.py

# Output: dist/sk.exe (Windows) or dist/sk (Linux/macOS)
```

## Build Options

```bash
# Dry run (show command without executing)
python build/nuitka-build.py --dry-run

# Build as directory (faster startup, multiple files)
python build/nuitka-build.py --no-onefile

# Custom output directory
python build/nuitka-build.py --output-dir release/
```

## How It Works

The build script:
1. Compiles `sk.py` as the entry point using Nuitka's `--standalone --onefile` mode
2. Includes all 33 dispatched scripts as data files (runpy needs `.py` source at runtime)
3. Includes `browse/` and `hooks/` packages as compiled modules
4. Bundles `browse/static/` as data directory for the web UI
5. Excludes optional heavy dependencies (scikit-learn, numpy, tkinter)

### Frozen Mode Detection

In the compiled binary, `sys.frozen = True`. The refactored `sk.py` detects this and uses
`runpy.run_path()` instead of `subprocess.run()` to dispatch commands — avoiding the need
for a separate Python interpreter.

## Expected Output Size

| Mode | Approximate Size |
|------|-----------------|
| Onefile | 15–25 MB |
| Standalone (dir) | 20–35 MB (total) |

## Troubleshooting

### Windows: "MSVC not found"
Install Visual Studio Build Tools: https://visualstudio.microsoft.com/downloads/#build-tools

### Linux: Missing gcc
```bash
sudo apt install gcc python3-dev  # Debian/Ubuntu
sudo dnf install gcc python3-devel  # Fedora
```

### macOS: Missing Xcode tools
```bash
xcode-select --install
```

### Antivirus false positive (Windows)
Some AV software flags Nuitka-compiled binaries. Solutions:
- Sign the executable with a code-signing certificate
- Use `--onefile-tempdir-spec="{CACHE_DIR}/sk"` to use a stable extraction path
