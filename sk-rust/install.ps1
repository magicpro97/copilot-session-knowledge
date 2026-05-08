# install.ps1 — Download and install sk-windows-x64.zip for copilot-session-knowledge
# Usage: irm https://raw.githubusercontent.com/magicpro97/copilot-session-knowledge/main/sk-rust/install.ps1 | iex

$ErrorActionPreference = "Stop"

$Repo        = "magicpro97/copilot-session-knowledge"
$Asset       = "sk-windows-x64.zip"
$InstallDir  = Join-Path $env:USERPROFILE ".copilot\bin"
$BinaryName  = "sk.exe"
$Dest        = Join-Path $InstallDir $BinaryName

# Fetch latest release tag
Write-Host "Fetching latest release info from GitHub..."
$ReleasesUrl = "https://api.github.com/repos/$Repo/releases/latest"
$Release = Invoke-RestMethod -Uri $ReleasesUrl -Headers @{ "User-Agent" = "install.ps1" }
$LatestTag = $Release.tag_name

if (-not $LatestTag) {
    Write-Error "Failed to determine latest release tag."
    exit 1
}

Write-Host "Latest release: $LatestTag"

$DownloadUrl  = "https://github.com/$Repo/releases/download/$LatestTag/$Asset"
$ChecksumUrl  = "$DownloadUrl.sha256"

# Create install directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
}

$ArchivePath = Join-Path $InstallDir "sk-download.zip"

Write-Host "Downloading $DownloadUrl ..."
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ArchivePath -UseBasicParsing

# Verify SHA256 checksum
Write-Host "Verifying checksum..."
$ChecksumLine = (Invoke-WebRequest -Uri $ChecksumUrl -UseBasicParsing).Content.Trim()
$ExpectedHash = ($ChecksumLine -split '\s+')[0].ToLower()
$ActualHash   = (Get-FileHash $ArchivePath -Algorithm SHA256).Hash.ToLower()
if ($ExpectedHash -ne $ActualHash) {
    Remove-Item $ArchivePath -Force
    Write-Error "Checksum mismatch! expected=$ExpectedHash got=$ActualHash"
    exit 1
}

# Extract binary from archive
$ExtractDir = Join-Path $InstallDir "sk-extract"
if (Test-Path $ExtractDir) { Remove-Item $ExtractDir -Recurse -Force }
Expand-Archive -Path $ArchivePath -DestinationPath $ExtractDir
Remove-Item $ArchivePath -Force

# Find and move the binary
$ExtractedExe = Get-ChildItem $ExtractDir -Filter "*.exe" | Select-Object -First 1
if (-not $ExtractedExe) {
    Write-Error "No .exe found in archive."
    exit 1
}
Move-Item $ExtractedExe.FullName $Dest -Force
Remove-Item $ExtractDir -Recurse -Force

Write-Host ""
Write-Host "✅ sk $LatestTag installed to $Dest"
Write-Host ""

# Add to user PATH if not already present
$CurrentPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$PathEntries = $CurrentPath -split ";"

if ($PathEntries -notcontains $InstallDir) {
    $NewPath = ($PathEntries + $InstallDir) -join ";"
    [System.Environment]::SetEnvironmentVariable("PATH", $NewPath, "User")
    Write-Host "✅ Added $InstallDir to your user PATH."
    Write-Host "   Restart your terminal (or open a new PowerShell window) for the change to take effect."
} else {
    Write-Host "✓ $InstallDir is already in your PATH."
}

Write-Host ""
Write-Host "Run 'sk --help' to get started."
