# Wait until the Garmin Coach server is actually accepting connections on port
# 8000, THEN open it in a clean app window. Launched hidden by start_app.bat so
# the browser never races ahead of the server (no "connection refused").
$ErrorActionPreference = 'SilentlyContinue'
$url = 'http://localhost:8000'

# Poll the port for up to ~48 s (120 x 400 ms). Break the moment it answers.
for ($i = 0; $i -lt 120; $i++) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', 8000)
        $client.Close()
        break
    } catch {
        Start-Sleep -Milliseconds 400
    }
}

# Prefer a clean "app" window (Edge, then Chrome); fall back to the default browser.
$opened = $false
foreach ($browser in 'msedge', 'chrome') {
    try {
        Start-Process $browser "--app=$url" -ErrorAction Stop
        $opened = $true
        break
    } catch { }
}
if (-not $opened) { Start-Process $url }
