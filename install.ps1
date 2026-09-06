#Requires -Version 5.0
# UTF-8 with BOM is required for Windows PowerShell 5.1 on Japanese Windows.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-InstallRoot {
    if ($PSScriptRoot) { return $PSScriptRoot }
    if ($MyInvocation.MyCommand.Path) {
        return (Split-Path -Parent $MyInvocation.MyCommand.Path)
    }
    return (Get-Location).Path
}

$Root = Get-InstallRoot
$Bin = Join-Path $Root 'bin'

Write-Host 'cloud-agent-sync installer'
Write-Host ('Root: ' + $Root)
Write-Host ''

function Test-Python {
    $commands = @(
        @{ File = 'py'; Args = @('-3', '--version') },
        @{ File = 'python'; Args = @('--version') },
        @{ File = 'python3'; Args = @('--version') }
    )
    foreach ($item in $commands) {
        $cmd = Get-Command $item.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $output = & $cmd.Source @($item.Args) 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -or $output -match 'Python') {
                return ($output.Trim() + '  (' + $cmd.Source + ')')
            }
        } catch {
            continue
        }
    }
    return $null
}

$python = Test-Python
if ($python) {
    Write-Host ('Python: ' + $python)
} else {
    Write-Host '[WARN] Python 3 was not found.'
    Write-Host '  Install Python 3 from https://www.python.org/downloads/'
    Write-Host '  Enable "Add python.exe to PATH".'
    Write-Host '  Existing Git / GitHub credentials will not be changed.'
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Write-Host ('Git: ' + (& git --version))
} else {
    Write-Host '[WARN] Git was not found. Install Git for Windows.'
}

if (-not (Test-Path -LiteralPath $Bin)) {
    throw ('bin directory is missing: ' + $Bin)
}

$normalizedBin = [System.IO.Path]::GetFullPath($Bin).TrimEnd('\')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $userPath) { $userPath = '' }

$parts = @()
if ($userPath.Trim() -ne '') {
    $parts = @($userPath.Split(';') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
}

$already = $false
foreach ($part in $parts) {
    try {
        $full = [System.IO.Path]::GetFullPath($part).TrimEnd('\')
    } catch {
        $full = $part.TrimEnd('\')
    }
    if ($full -eq $normalizedBin) {
        $already = $true
        break
    }
}

if ($already) {
    Write-Host ('PATH: already registered: ' + $normalizedBin)
} else {
    if ($userPath.Trim() -eq '') {
        $newPath = $normalizedBin
    } else {
        $newPath = $userPath.TrimEnd(';') + ';' + $normalizedBin
    }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host 'PATH: appended to the user PATH'
    Write-Host ('      ' + $normalizedBin)
}

if ($env:Path -notlike ('*' + $normalizedBin + '*')) {
    $env:Path = $env:Path.TrimEnd(';') + ';' + $normalizedBin
}

Write-Host ''
Write-Host 'PowerShell Profile was not changed.'
Write-Host 'Existing Git remotes, branches, and credentials were not changed.'
Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Restart Cursor and this terminal'
Write-Host '  2. Run: sync --doctor'
Write-Host '  3. First time: sync   (choose repositories from the list)'
Write-Host '  4. After that: sync'
Write-Host ''
Write-Host 'From Cursor:'
Write-Host '  Terminal -> Run Task -> Git Sync All'
Write-Host ''
Write-Host 'Install finished.'
