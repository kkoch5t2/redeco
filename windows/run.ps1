[CmdletBinding()]
param([string]$Config = (Join-Path $env:LOCALAPPDATA 'Redeco\config.json'))
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
& (Join-Path $repo '.venv-windows\Scripts\python.exe') (Join-Path $PSScriptRoot 'runner.py') --config $Config
exit $LASTEXITCODE
