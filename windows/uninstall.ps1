[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = "Redeco-MCP-$sid"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Write-Output 'Redeco auto-start removed. Configuration, audit, and source files retained.'
