#Requires -Version 5.0
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cmd = Join-Path $Root "bin\sync.cmd"
& $cmd @args
exit $LASTEXITCODE
