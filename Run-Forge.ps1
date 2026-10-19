# art-forge launcher (python -m forge): fills the local ComfyUI queue with the jobs of an
# order and keeps it going for hours, detached from this console (it goes on after the
# terminal that started it is gone).
#
#   .\Run-Forge.ps1 -Order <order.json> -Out <folder>            start (or resume) in the background
#   .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Check     check the order, then the environment
#   .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Estimate  job counts and estimated GPU hours
#   .\Run-Forge.ps1 -Out <folder> -Status                        progress, failures, GPU lock, what a local
#                                                                GPU job waits for, last log lines
#   .\Run-Forge.ps1 -Out <folder> -Stop                          stop after the prompts already queued
#   .\Run-Forge.ps1 -Out <folder> -Stop -Now                     stop now (queued prompts are collected on resume)
#
# Other parameters: -Foreground (runs in this console), -RetryFailed, -Limit <n>,
# -Only <families>, -Server <url> (the ComfyUI address; default: COMFY_SERVER, else the
# configuration's comfy_url). The output folder belongs to the game and must be outside this
# repository. The machine configuration is forge.local.json next to this script.
#
# The running factory uses a snapshot of the program, the workflows, the local tools, the
# order and the configuration copied to <Out>\program, so editing them does not change a
# run in progress: edit, then -Stop and start again (finished jobs are skipped by the
# manifest). Relative input paths of the order still resolve against the order's own folder,
# and relative paths of forge.local.json against this repository (as with -Check).
#
# -Check prints two results: "order: valid" or its problems, then the environment as
# "verified", "not ready" (with the list) or "not verified" (the server did not answer:
# local validation done, environment not checked). It reads only GET /object_info from the
# server. Exit code 0 (valid and verified), 1 (problems or not ready), 3 (not verified).
#
# A run holds the machine-wide GPU lock (FORGE_LOCK_DIR, else %USERPROFILE%\.art-forge\gpu.lock):
# a second factory on this machine refuses to start while one runs. A detached process does
# not inherit this console's environment (WMI gives it the user's stored one), so the lock
# folder and the server address are passed on its command line (--lock-dir, --server).
param(
    [string]$Order = "",
    [string]$Out = "",
    [switch]$Check,
    [switch]$Estimate,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Now,
    [switch]$Foreground,
    [switch]$RetryFailed,
    [int]$Limit = 6,
    [string]$Only = "",
    [string]$Server = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$configPath = Join-Path $root "forge.local.json"

function Get-FullPath([string]$path) {
    return [System.IO.Path]::GetFullPath([System.IO.Path]::Combine((Get-Location).Path, $path))
}

# One argument of a Windows command line: quoted when it has a space or a backslash, with
# the backslashes before the closing quote doubled (a quoted C:\ would otherwise escape
# its own closing quote).
# BEGIN Format-Argument
function Format-Argument([string]$arg) {
    if ($arg -notmatch "[\s\\]") { return $arg }
    return "`"" + ($arg -replace '(\\+)$', '$1$1') + "`""
}
# END Format-Argument

function Test-Inside([string]$path, [string]$folder) {
    $p = $path.TrimEnd("\") + "\"
    $f = $folder.TrimEnd("\") + "\"
    return $p.StartsWith($f, [System.StringComparison]::OrdinalIgnoreCase)
}

if ($Out -eq "") { throw "-Out <folder> is required (a folder outside this repository)" }
$Out = Get-FullPath $Out
if (Test-Inside $Out $root) { throw "the output folder must be outside this repository: $Out" }
if (-not ($Status -or $Stop) -and $Order -eq "") { throw "-Order <order.json> is required" }

# Python: the configuration's "python" if set, else the python on the PATH.
$python = ""
if (Test-Path $configPath) {
    $config = Get-Content -Raw $configPath | ConvertFrom-Json
    if ($config.python) {
        $python = [string]$config.python
        if (-not [System.IO.Path]::IsPathRooted($python)) { $python = Join-Path $root $python }
        if (-not (Test-Path $python)) { throw "python of forge.local.json not found: $python" }
    }
}
if ($python -eq "") { $python = (Get-Command python -ErrorAction Stop).Source }

$pidFile = Join-Path $Out "factory.pid"
$stopFile = Join-Path $Out "STOP"

function Get-FactoryProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $n = 0
    if (-not [int]::TryParse((Get-Content -Raw $pidFile).Trim(), [ref]$n)) { return $null }
    $p = Get-Process -Id $n -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like "python*") { return $p }
    return $null
}

# The lock folder as the factory resolves it (forge/gpu_lock.py lock_dir): FORGE_LOCK_DIR,
# else this user's default folder.
function Get-LockDir {
    $code = "import sys; sys.path.insert(0, sys.argv[1]); from forge import gpu_lock; print(gpu_lock.lock_dir())"
    $dir = (& $python -c $code $root | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $dir -eq "") { throw "could not resolve the GPU lock folder" }
    return $dir
}

function Get-LockPath {
    return (Join-Path (Get-LockDir) "gpu.lock")
}

if ($Status) {
    $p = Get-FactoryProcess
    if ($p) { Write-Host "Factory running (pid $($p.Id))." } else { Write-Host "Factory not running." }
    # Who took the machine-wide GPU lock (the file may be left by a process that is gone).
    $lockPath = Get-LockPath
    if (Test-Path $lockPath) {
        # Opened sharing read and write: the holder keeps the file open.
        $fs = New-Object System.IO.FileStream($lockPath, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $reader = New-Object System.IO.StreamReader($fs)
        try { $info = $reader.ReadToEnd().Trim() } finally { $reader.Close() }
        if ($info -ne "") {
            $holder = $info | ConvertFrom-Json
            $alive = Get-Process -Id ([int]$holder.pid) -ErrorAction SilentlyContinue
            $state = "process gone: stale"
            if ($alive) { $state = "running" }
            Write-Host "GPU lock: pid $($holder.pid) ($($holder.what), since $($holder.since), $state)."
        } else { Write-Host "GPU lock: free." }
    } else { Write-Host "GPU lock: free." }
    $state = Join-Path $Out "state.json"
    if (Test-Path $state) {
        $raw = Get-Content -Raw $state
        $doc = $raw | ConvertFrom-Json
        # What a local GPU job is waiting for (the whole ComfyUI queue empty, /free, other
        # processes on the card), or which one is running.
        if ($doc.gpu_wait) {
            $w = $doc.gpu_wait
            $word = "waits"
            if ($w.stalled) { $word = "STALLED, waits" }
            Write-Host "Local GPU job $($w.job) $word since $($w.since) ($([int]$w.seconds) s): $($w.reason)."
            if ($w.holders -and $w.holders.other_prompts) {
                Write-Host "  Prompts of other clients in the queue: $($w.holders.other_prompts -join ', ')"
            }
        }
        if ($doc.local_gpu_running) {
            $r = $doc.local_gpu_running
            Write-Host "Local GPU job $($r.job) running since $($r.since) (tool $($r.tool))."
        }
        $raw | Write-Host
    }
    $log = Join-Path $Out "factory.log"
    if (Test-Path $log) { Get-Content $log -Tail 15 | Write-Host }
    exit 0
}

if ($Stop) {
    if (-not (Test-Path $Out)) { Write-Host "Factory not running (no output folder)."; exit 0 }
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    $p = Get-FactoryProcess
    if ($Now -and $p) {
        Stop-Process -Id $p.Id -Force
        Remove-Item -Force $stopFile -ErrorAction SilentlyContinue
        Write-Host "Factory stopped now (pid $($p.Id)); prompts already queued are collected on the next start."
    } elseif ($p) {
        Write-Host "Stop requested: the factory submits nothing more and exits after its queued prompts."
    } else {
        Remove-Item -Force $stopFile -ErrorAction SilentlyContinue
        Write-Host "Factory not running."
    }
    exit 0
}

$Order = (Resolve-Path $Order).Path
$common = @("--order", $Order, "--out", $Out)
if (Test-Path $configPath) { $common += @("--config", $configPath) }
if ($Only -ne "") { $common += @("--only", $Only) }
if ($Server -eq "" -and $env:COMFY_SERVER) { $Server = $env:COMFY_SERVER }
if ($Server -ne "") { $common += @("--server", $Server) }

if ($Check -or $Estimate) {
    $mode = "--estimate"
    if ($Check) { $mode = "--check" }
    Push-Location $root
    try { & $python -m forge @common $mode } finally { Pop-Location }
    exit $LASTEXITCODE
}

$p = Get-FactoryProcess
if ($p) { Write-Host "Factory already running (pid $($p.Id)). Use -Status or -Stop."; exit 0 }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
# The output folder may be reached through a junction: use the real folder, so that the
# detached factory does not depend on the junction, and refuse it if it is really inside.
$Out = (& $python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" $Out).Trim()
if (Test-Inside $Out $root) { throw "the output folder must be outside this repository: $Out" }
$pidFile = Join-Path $Out "factory.pid"
$stopFile = Join-Path $Out "STOP"
Remove-Item -Force $stopFile -ErrorAction SilentlyContinue

# Snapshot of the program, the workflows, the local tools, the order and the configuration.
$program = Join-Path $Out "program"
$programForge = Join-Path $program "forge"
$programWorkflows = Join-Path $programForge "workflows"
if (Test-Path $programForge) { Remove-Item -Recurse -Force $programForge }
New-Item -ItemType Directory -Force -Path $programWorkflows | Out-Null
Copy-Item -Force (Join-Path $root "forge\*.py") $programForge
Copy-Item -Force (Join-Path $root "forge\workflows\*.json") $programWorkflows
# The local tools (Blender scripts, the Godot review project, the rig entry).
$programTools = Join-Path $program "tools"
if (Test-Path $programTools) { Remove-Item -Recurse -Force $programTools }
foreach ($sub in @("blender", "rig", "review_godot")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $programTools $sub) | Out-Null
}
Copy-Item -Force (Join-Path $root "tools\blender\*.py") (Join-Path $programTools "blender")
Copy-Item -Force (Join-Path $root "tools\rig\*.py") (Join-Path $programTools "rig")
# Every file of the Godot review project (the humanoid's sheet, the layout sheet, the item
# sheet), not its .godot cache folder.
Get-ChildItem -File (Join-Path $root "tools\review_godot") | ForEach-Object {
    Copy-Item -Force $_.FullName (Join-Path $programTools "review_godot")
}
# The text-to-motion entry and the motion data the factory ships.
if (Test-Path (Join-Path $root "tools\motion")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $programTools "motion") | Out-Null
    Copy-Item -Force (Join-Path $root "tools\motion\*.py") (Join-Path $programTools "motion")
}
$programData = Join-Path $program "data"
if (Test-Path $programData) { Remove-Item -Recurse -Force $programData }
if (Test-Path (Join-Path $root "data")) { Copy-Item -Recurse -Force (Join-Path $root "data") $programData }
Copy-Item -Force $Order (Join-Path $program "order.json")
$orderBase = Split-Path -Parent $Order

$factoryArgs = @("-m", "forge", "--order", "$program\order.json", "--order-base", $orderBase,
    "--out", $Out, "--limit", "$Limit", "--lock-dir", (Get-LockDir))
if (Test-Path $configPath) {
    Copy-Item -Force $configPath (Join-Path $program "forge.local.json")
    # Relative paths of the copy still resolve against this repository, as with -Check.
    $factoryArgs += @("--config", "$program\forge.local.json", "--config-base", $root)
}
if ($Only -ne "") { $factoryArgs += @("--only", $Only) }
if ($Server -ne "") { $factoryArgs += @("--server", $Server) }
if ($RetryFailed) { $factoryArgs += "--retry-failed" }

if ($Foreground) {
    Push-Location $program
    try { & $python @factoryArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}

# Detached: created by WMI, so it does not belong to this console or its job object, and
# with pythonw it has no window. Its own log is <Out>\factory.log.
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$line = "`"$pythonw`" " + (($factoryArgs | ForEach-Object { Format-Argument $_ }) -join " ")
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $line; CurrentDirectory = $program }
if ($r.ReturnValue -ne 0) { throw "could not start the factory (Win32_Process.Create returned $($r.ReturnValue))" }
Write-Host "Factory started in the background (pid $($r.ProcessId)). Log: $Out\factory.log"
Write-Host "Progress: .\Run-Forge.ps1 -Out `"$Out`" -Status    Stop: -Stop"
