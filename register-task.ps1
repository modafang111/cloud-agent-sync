#Requires -Version 5.0
# UTF-8 with BOM is required for Windows PowerShell 5.1.
param(
    [string]$TaskName = 'cloud-agent-sync',
    [string]$Time = '20:00',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($PSScriptRoot) {
    $Root = $PSScriptRoot
} else {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$Cmd = Join-Path $Root 'bin\sync.cmd'
if (-not (Test-Path -LiteralPath $Cmd)) {
    throw ('sync.cmd not found: ' + $Cmd)
}

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host ('Removed scheduled task: ' + $TaskName)
    } else {
        Write-Host ('Task not found: ' + $TaskName)
    }
    exit 0
}

$action = New-ScheduledTaskAction -Execute $Cmd -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host ('Registered scheduled task: ' + $TaskName)
Write-Host ('Command: ' + $Cmd)
Write-Host ('Daily at: ' + $Time)
Write-Host 'This task only runs sync. It does not call AI, --provision, or --init.'
Write-Host 'GitHub auth works best while you are logged on to Windows.'
