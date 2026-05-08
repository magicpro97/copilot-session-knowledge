# install.ps1 — Install sk binary from GitHub Releases (Windows)
# Usage: irm https://raw.githubusercontent.com/magicpro97/copilot-session-knowledge/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$Repo = "magicpro97/copilot-session-knowledge"
$Binary = "sk"

# --- Architecture detection ---

$Arch = if ([System.Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$Asset = "$Binary-windows-$Arch.zip"

# --- Version detection ---

if ($env:SK_VERSION) {
    $Version = $env:SK_VERSION
} else {
    try {
        $Release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
        $Version = $Release.tag_name
    } catch {
        Write-Error "Could not determine latest version: $_"
        exit 1
    }
}

$Url = "https://github.com/$Repo/releases/download/$Version/$Asset"
$ChecksumUrl = "$Url.sha256"

# --- Install directory ---

$InstallDir = if ($env:SK_INSTALL_DIR) { $env:SK_INSTALL_DIR } else { "$env:USERPROFILE\.copilot\bin" }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# --- Download ---

$TempDir = Join-Path $env:TEMP "sk-install-$(Get-Random)"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

try {
    Write-Host "Installing sk $Version (windows/$Arch)..."
    Write-Host "  Downloading: $Url"

    $ZipPath = Join-Path $TempDir $Asset
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing

    # --- Checksum verification ---

    try {
        $ChecksumPath = Join-Path $TempDir "$Asset.sha256"
        Invoke-WebRequest -Uri $ChecksumUrl -OutFile $ChecksumPath -UseBasicParsing
        $Expected = (Get-Content $ChecksumPath -Raw).Trim().Split()[0]
        $Actual = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()

        if ($Expected -ne $Actual) {
            Write-Error "Checksum mismatch!`n  Expected: $Expected`n  Got:      $Actual"
            exit 1
        }
        Write-Host "  Checksum verified ✓"
    } catch {
        Write-Host "  Warning: Checksum verification skipped"
    }

    # --- Extract ---

    Expand-Archive -Path $ZipPath -DestinationPath $InstallDir -Force
    Write-Host "  Installed: $InstallDir\$Binary.exe"
    Write-Host ""

    # --- PATH setup ---

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$UserPath", "User")
        Write-Host "✅ Added $InstallDir to user PATH"
        Write-Host "   Restart your terminal, then run: sk --version"
    } else {
        Write-Host "✅ sk is ready! Run: sk --version"
    }

    # Also update current session PATH
    if ($env:Path -notlike "*$InstallDir*") {
        $env:Path = "$InstallDir;$env:Path"
    }
} finally {
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
}
