[CmdletBinding()]
param(
    [string[]]$Roots,
    [ValidateRange(1024,65535)][int]$Port = 8770,
    [switch]$AllowAdmin,
    [switch]$NoAutoStart,
    [switch]$ConfigureOnly
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$state = Join-Path $env:LOCALAPPDATA 'Redeco'
$configPath = Join-Path $state 'config.json'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$old = $null
if (Test-Path $configPath) { $old = Get-Content -Raw $configPath | ConvertFrom-Json }
if (-not $PSBoundParameters.ContainsKey('AllowAdmin') -and $old) { $AllowAdmin = [bool]$old.allow_admin }
if (-not $PSBoundParameters.ContainsKey('Port') -and $old) { $Port = [int]$old.port }
if ($AllowAdmin -and -not $isAdmin) { throw 'Run PowerShell as Administrator once to configure -AllowAdmin.' }
if (-not $Roots) {
    if ($old) { $Roots = @($old.roots) }
    else { $Roots = @(Join-Path $env:USERPROFILE 'Sites'); New-Item -ItemType Directory -Force $Roots[0] | Out-Null }
}
$resolved = @($Roots | ForEach-Object {
    $p = (Resolve-Path -LiteralPath $_).ProviderPath
    if (-not (Test-Path -LiteralPath $p -PathType Container)) { throw 'Each root must be an existing directory.' }
    if ($p.StartsWith('\\') -or $p.Contains(';') -or $p.Substring(2).Contains(':')) { throw 'Use local directories without semicolons or alternate data streams.' }
    $p
})
# Keep executable state outside Git, restricted to this user and SYSTEM.
New-Item -ItemType Directory -Force $state | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $state /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict state directory permissions.' }
$python = Join-Path $repo '.venv-windows\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv (Join-Path $repo '.venv-windows')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ with the py launcher is required.' }
}
& $python -m pip install -r (Join-Path $repo 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$config = [ordered]@{ roots = $resolved; port = $Port; allow_admin = [bool]$AllowAdmin; repository = $repo }
$temp = Join-Path $state 'config.new'
[IO.File]::WriteAllText($temp, ($config | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
Move-Item -LiteralPath $temp -Destination $configPath -Force
if (-not $NoAutoStart) {
    $taskName = "Redeco-MCP-$sid"
    $pythonw = Join-Path $repo '.venv-windows\Scripts\pythonw.exe'
    $runner = Join-Path $PSScriptRoot 'runner.py'
    $arguments = '"' + $runner + '" --config "' + $configPath + '"'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repo
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $level = 'Limited'
    if ($AllowAdmin) { $level = 'Highest' }
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel $level
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
            if ((Get-Date) -gt $deadline) { throw 'Previous Redeco task did not stop.' }
            Start-Sleep -Milliseconds 200
        }
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    if (-not $ConfigureOnly) {
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds(20)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            $client = New-Object Net.Sockets.TcpClient
            try { $client.Connect('127.0.0.1', $Port); $ready = $true; break }
            catch { Start-Sleep -Milliseconds 500 }
            finally { $client.Dispose() }
        }
        if (-not $ready) { throw 'MCP did not start. Inspect LOCALAPPDATA\Redeco\server.log.' }
        & $python (Join-Path $PSScriptRoot 'check_runtime.py') --port $Port
        if ($LASTEXITCODE -ne 0) { throw 'MCP protocol verification failed.' }
    }
    Write-Output "Registered $taskName (RunLevel=$level)."
}
Write-Output "Configured Windows MCP: http://127.0.0.1:$Port/mcp"
