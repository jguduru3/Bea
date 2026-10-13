# Remove Bea from this PC. Keeps backend\.env and the database unless you pass -All.
#   powershell -ExecutionPolicy Bypass -File .\windows\uninstall.ps1 [-All]
param([switch]$All)
$ErrorActionPreference = 'Continue'
Get-Process Bea -ErrorAction SilentlyContinue | Stop-Process -Force
Unregister-ScheduledTask -TaskName 'Bea backend' -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA 'Programs\Bea') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Bea.lnk') -ErrorAction SilentlyContinue
if ($All) {
  Remove-Item -Recurse -Force (Join-Path $env:APPDATA 'Bea'), (Join-Path $env:LOCALAPPDATA 'Bea-server') -ErrorAction SilentlyContinue
  Write-Host 'Removed settings, conversations, memory and logs.'
}
Write-Host 'Bea has been uninstalled.'
