# One-command setup of Bea on Windows 10/11:
#   1. backend in a Python virtualenv, with your API keys in backend\.env (Claude = brain, Sarvam = voice)
#   2. Bea's own browser (Chromium for Playwright) for web tasks
#   3. backend started now and at every sign-in (a Scheduled Task, local only: 127.0.0.1:8080)
#   4. Bea.exe built (with the offline "Hey Bea" wake word model) and installed for this user
#   5. the app connected to the backend automatically (no token to copy)
#
#   powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Desktop = Join-Path $Root 'desktop'
$Data = Join-Path $env:LOCALAPPDATA 'Bea-server'
$Logs = Join-Path $Data 'logs'
$AppData = Join-Path $env:APPDATA 'Bea'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\Bea'
$Port = 8080
$TaskName = 'Bea backend'
$Utf8 = New-Object System.Text.UTF8Encoding($false)   # no BOM: Python and Node read these files

function Say($m) { Write-Host "`n> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Die($m) { Write-Host "  X  $m" -ForegroundColor Red; exit 1 }
function Ask($p, $d = '') { $v = Read-Host "  $p"; if ([string]::IsNullOrWhiteSpace($v)) { $d } else { $v } }
function Secret($p) {
  $s = Read-Host "  $p" -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
$EnvFile = Join-Path $Backend '.env'
function Current($k) { $l = (Get-Content $EnvFile) | Where-Object { $_ -match "^$k=" } | Select-Object -First 1; if ($l) { $l.Substring($k.Length + 1) } else { '' } }
function SetEnv($k, $v) {
  $lines = [System.Collections.Generic.List[string]](Get-Content $EnvFile)
  $i = $lines.FindIndex([Predicate[string]] { param($l) $l.StartsWith("$k=") })
  if ($i -ge 0) { $lines[$i] = "$k=$v" } else { $lines.Add("$k=$v") }
  [IO.File]::WriteAllText($EnvFile, ($lines -join "`n") + "`n", $Utf8)
}
function NewSecret { & $Py -c 'import secrets; print(secrets.token_urlsafe(32))' }

Write-Host "Setting up Bea on $env:COMPUTERNAME"

# ------------------------------------------------------------ prerequisites
Say 'Checking tools'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Die 'Node.js 20+ is needed: https://nodejs.org (or: winget install OpenJS.NodeJS.LTS)' }
if ([int](node -p 'process.versions.node.split(".")[0]') -lt 20) { Die "Node.js 20+ is needed (found $(node -v))." }
Ok "Node $(node -v)"
$Py = $null
foreach ($c in @('py -3.13', 'py -3.12', 'py -3.11', 'py -3.10', 'python')) {
  $exe, $arg = $c.Split(' ', 2)
  if (Get-Command $exe -ErrorAction SilentlyContinue) {
    $p = & $exe @($arg | Where-Object { $_ }) -c 'import sys; print(sys.executable if sys.version_info >= (3,10) else "")' 2>$null
    if ($p) { $Py = $p.Trim(); break }
  }
}
if (-not $Py) { Die 'Python 3.10+ is needed: https://python.org (or: winget install Python.Python.3.12)' }
Ok "Python $(& $Py -c 'import platform; print(platform.python_version())')"

# ------------------------------------------------------------ backend
Say 'Setting up the backend'
New-Item -ItemType Directory -Force $Data, $Logs, $AppData | Out-Null
if (-not (Test-Path "$Backend\.venv\Scripts\python.exe")) { & $Py -m venv "$Backend\.venv" }
$VPy = "$Backend\.venv\Scripts\python.exe"
& $VPy -m pip install --quiet --upgrade pip
& $VPy -m pip install --quiet -e $Backend
if ($LASTEXITCODE) { Die 'Installing the Python packages failed (see above).' }
Ok 'Python packages installed'
& $VPy -m playwright install chromium | Out-Null
Ok "Bea's browser installed (Chromium for Playwright)"

if (-not (Test-Path $EnvFile)) { [IO.File]::WriteAllText($EnvFile, [IO.File]::ReadAllText("$Backend\.env.example"), $Utf8) }
# Claude is Bea's brain (chat and agent), OpenAI an optional backup for chat, Sarvam her voice.
# Keys can also be added later in the app: Settings > API keys.
SetEnv 'POM_LLM_PROVIDER' 'anthropic'
SetEnv 'POM_LLM_FALLBACK_PROVIDER' 'openai'
SetEnv 'POM_ALLOW_DEVICE_KEY_MANAGEMENT' 'true'
if (-not (Current 'POM_ANTHROPIC_API_KEY')) {
  Write-Host '  Bea''s brain is Claude. Paste an Anthropic API key (starts with sk-ant-api), or press Enter to add it later in Settings.'
  $k = Secret 'Anthropic API key'; if ($k) { SetEnv 'POM_ANTHROPIC_API_KEY' $k }
}
if (-not (Current 'POM_OPENAI_API_KEY')) { $k = Secret 'OpenAI API key for backup (Enter to skip)'; if ($k) { SetEnv 'POM_OPENAI_API_KEY' $k } }
if (-not (Current 'POM_SARVAM_API_KEY')) { $k = Secret 'Sarvam AI API key for voice (Enter to skip)'; if ($k) { SetEnv 'POM_SARVAM_API_KEY' $k } }
$Token = Current 'POM_BOOTSTRAP_DEVICE_TOKEN'
if (-not $Token -or $Token -like 'pom_replace*') { $Token = "pom_$(NewSecret)"; SetEnv 'POM_BOOTSTRAP_DEVICE_TOKEN' $Token }
$Admin = Current 'POM_ADMIN_API_KEY'
if (-not $Admin -or $Admin -like 'change-me*') { SetEnv 'POM_ADMIN_API_KEY' (NewSecret) }
SetEnv 'POM_DATABASE_URL' ("sqlite+aiosqlite:///" + ($Data -replace '\\', '/') + '/pom.db')
SetEnv 'POM_DATA_DIR' (($Data -replace '\\', '/') + '/data')
SetEnv 'POM_SECRET_FILE' (($Data -replace '\\', '/') + '/.pom_secret')
# Only you can read the settings file.
icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
Ok 'Settings saved in backend\.env (readable only by you)'

# ------------------------------------------------------------ keep the backend running
Say 'Starting the backend (and at every sign-in)'
$Run = "Set-Location '$Backend'; & '$VPy' -m uvicorn app.main:app --host 127.0.0.1 --port $Port *>> '$Logs\backend.log'"
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `"$Run`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Bea desktop assistant backend (local only)' | Out-Null
Start-ScheduledTask -TaskName $TaskName
$ready = $null
foreach ($i in 1..40) { try { $ready = Invoke-RestMethod "http://127.0.0.1:$Port/readyz" -TimeoutSec 2; break } catch { Start-Sleep 1 } }
if (-not $ready) { Die "The backend didn't start. See $Logs\backend.log" }
Ok "Backend running on http://127.0.0.1:$Port (chat: $($ready.chat_configured), voice: $($ready.voice_configured))"

# ------------------------------------------------------------ desktop app
Say 'Building Bea.exe'
Push-Location $Desktop
try {
  if (Test-Path package-lock.json) { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
  if (-not (Test-Path 'node_modules\electron\dist\electron.exe')) { node node_modules\electron\install.js }
  npm run --silent fetch-wake-model
  $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
  npx electron-builder --win dir --publish never
  if ($LASTEXITCODE) {
    # Stamping the icon onto Bea.exe needs a helper whose download contains symbolic links, which
    # Windows only allows with Developer Mode or admin rights. Build without that step instead.
    Write-Host '  Retrying without stamping the icon onto Bea.exe (enable Windows Developer Mode to keep it).' -ForegroundColor Yellow
    npx electron-builder --win dir --publish never -c.win.signAndEditExecutable=false
    if ($LASTEXITCODE) { Die 'Building the app failed (see above).' }
  }
} finally { Pop-Location }
$Built = Get-ChildItem "$Desktop\dist" -Directory | Where-Object { $_.Name -like 'win*-unpacked' } | Select-Object -First 1
if (-not $Built) { Die 'Build finished but dist\win-unpacked was not found.' }
Get-Process Bea -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Copy-Item -Recurse $Built.FullName $InstallDir
$Exe = Join-Path $InstallDir 'Bea.exe'
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Bea.lnk'))
$Lnk.TargetPath = $Exe; $Lnk.WorkingDirectory = $InstallDir; $Lnk.Description = 'Bea, your desktop assistant'; $Lnk.Save()
Ok "Installed to $InstallDir (Start menu: Bea)"

# Hand the app its server address and token once; the app encrypts the token and deletes this file.
$Name = Ask 'What should Bea call you?'
$Wake = Ask 'Listen for the wake word "Hey Bea"? [Y/n]' 'y'
$Prov = @{ backendUrl = "http://127.0.0.1:$Port"; token = $Token; userName = $Name; launchAtLogin = $true; agentMode = $true; useBuiltInServer = $false; wakeWord = -not $Wake.ToLower().StartsWith('n') } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $AppData 'provision.json'), $Prov, $Utf8)
Ok 'App connected to the backend'

Say 'Launching Bea'
Start-Process $Exe
Write-Host @"

  Bea is on your desktop.
  - Double-click her or press Ctrl+Shift+Space to chat. Say "Hey Bea" to talk. Right-click for moves.
  - Give her tasks: she plans, researches, uses her browser and your apps, and asks before acting.
  - Bea Hub (tray icon > Bea Hub) shows her activity, approvals, goals, schedules and connections.
  - The first time she listens, Windows may ask for microphone access. Allow it.

  Backend logs:  $Logs\backend.log
  API keys:      $EnvFile   (then restart: Start-ScheduledTask -TaskName '$TaskName')
  Uninstall:     powershell -ExecutionPolicy Bypass -File .\windows\uninstall.ps1
"@
