#Requires -Version 5.0
<#
.SYNOPSIS
  cloud-agent-sync をユーザー PATH に追加します。既存の PATH / PowerShell Profile は破壊しません。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"

Write-Host "cloud-agent-sync インストーラ"
Write-Host "管理ディレクトリ: $Root"
Write-Host ""

function Test-Python {
    $commands = @(
        @{ File = "py"; Args = @("-3", "--version") },
        @{ File = "python"; Args = @("--version") },
        @{ File = "python3"; Args = @("--version") }
    )
    foreach ($item in $commands) {
        $cmd = Get-Command $item.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $output = & $cmd.Source @($item.Args) 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -or $output -match "Python") {
                return ($output.Trim() + "  (" + $cmd.Source + ")")
            }
        } catch {
            continue
        }
    }
    return $null
}

$python = Test-Python
if ($python) {
    Write-Host "Python: $python"
} else {
    Write-Host "[警告] Python 3 が見つかりません。"
    Write-Host "  https://www.python.org/downloads/ からインストールし、"
    Write-Host "  'Add python.exe to PATH' を有効にしてください。"
    Write-Host "  既存の Git / GitHub 認証は変更しません。"
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Write-Host ("Git: " + (& git --version))
} else {
    Write-Host "[警告] Git が見つかりません。Git for Windows をインストールしてください。"
}

if (-not (Test-Path -LiteralPath $Bin)) {
    throw "bin ディレクトリがありません: $Bin"
}

$normalizedBin = [System.IO.Path]::GetFullPath($Bin).TrimEnd("\")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }

$parts = @()
if ($userPath.Trim() -ne "") {
    $parts = @($userPath.Split(";") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
}

$already = $false
foreach ($part in $parts) {
    try {
        $full = [System.IO.Path]::GetFullPath($part).TrimEnd("\")
    } catch {
        $full = $part.TrimEnd("\")
    }
    if ($full -eq $normalizedBin) {
        $already = $true
        break
    }
}

if ($already) {
    Write-Host "PATH: 既に登録済みです ($normalizedBin)"
} else {
    $newPath = if ($userPath.Trim() -eq "") { $normalizedBin } else { $userPath.TrimEnd(";") + ";" + $normalizedBin }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "PATH: ユーザー PATH の末尾へ追加しました"
    Write-Host "      $normalizedBin"
}

if ($env:Path -notlike ("*" + $normalizedBin + "*")) {
    $env:Path = $env:Path.TrimEnd(";") + ";" + $normalizedBin
}

Write-Host ""
Write-Host "PowerShell Profile は変更していません。"
Write-Host "既存の Git remote / branch / 認証設定も変更していません。"
Write-Host ""
Write-Host "次の手順:"
Write-Host "  1. Cursor とターミナルを再起動する"
Write-Host "  2. どこでも sync --doctor で環境確認"
Write-Host "  3. 初回は sync  （検出一覧から同期対象を選ぶ）"
Write-Host "  4. 以後は sync  と入力するだけ"
Write-Host ""
Write-Host "Cursor から実行する場合:"
Write-Host "  Terminal → Run Task → Git Sync All"
Write-Host ""
Write-Host "インストール完了。"
