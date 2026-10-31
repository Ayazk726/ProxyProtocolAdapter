<#
Build a single-file Windows executable for the Proxy Protocol Adapter GUI.

Run from this directory:
    powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

The result is dist\ProxyProtocolAdapter.exe. Keep proxy_nat.json next to the exe.
#>
$ErrorActionPreference = "Stop"

$python = "python"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    $python = "py"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --uac-admin `
    --icon icons\B_bridge.ico `
    --add-data "icons;icons" `
    --name ProxyProtocolAdapter `
    --collect-all pydivert `
    --collect-all pystray `
    --collect-all PIL `
    --hidden-import proxy_nat `
    --hidden-import proxy_protocol `
    proxy_nat_gui.py

New-Item -ItemType Directory -Force dist\logs | Out-Null
New-Item -ItemType Directory -Force dist\config | Out-Null
Copy-Item -Force config\proxy_nat.json dist\config\proxy_nat.json
Copy-Item -Force README.md dist\README.md
Remove-Item -Force dist\proxy_nat.json,dist\proxy_nat.log,dist\proxy_nat_stderr.log,dist\proxy_nat.pid -ErrorAction SilentlyContinue
Write-Host "Built dist\ProxyProtocolAdapter.exe"
