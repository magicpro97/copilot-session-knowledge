# Example sk retry-listener hook — append retry events to a local log file.
#
# Install: copy to ~\.copilot\hooks\sk-retry-listener.ps1
# Or set $env:SK_RETRY_LISTENER to the full path of this script.
#
# Respond on stdout with:
#   {"abort": true}                       — abort the retry sequence
#   {"delay_override_seconds": 5.0}       — override the computed delay
# Anything else (or empty stdout) is treated as "observe" (no change).

$payload = $input | ConvertFrom-Json
$logPath = Join-Path $env:USERPROFILE ".copilot\retry-events.log"
Add-Content -Path $logPath -Value ($payload | ConvertTo-Json -Compress)
