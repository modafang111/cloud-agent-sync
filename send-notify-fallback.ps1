#Requires -Version 5.0
# UTF-8 with BOM is required for Windows PowerShell 5.1.
param(
    [string]$Reason = 'cloud-agent-sync failed before Python could send mail.'
)

$ErrorActionPreference = 'Stop'
if ($PSScriptRoot) {
    $Root = $PSScriptRoot
} else {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$localPath = Join-Path $Root 'notify.local.json'
$configPath = Join-Path $Root 'config.json'
if (-not (Test-Path -LiteralPath $localPath)) {
    Write-Host 'notify.local.json not found; cannot send fallback mail.'
    exit 1
}

$local = Get-Content -LiteralPath $localPath -Raw -Encoding UTF8 | ConvertFrom-Json
$config = $null
if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

$to = [string]$local.notify_email
if (-not $to -and $config) { $to = [string]$config.notify_email }
$hostName = [string]$local.smtp_host
if (-not $hostName -and $config) { $hostName = [string]$config.smtp_host }
if (-not $hostName) { $hostName = 'smtp.gmail.com' }
$port = 587
if ($local.smtp_port) { $port = [int]$local.smtp_port }
elseif ($config -and $config.smtp_port) { $port = [int]$config.smtp_port }
$user = [string]$local.smtp_user
if (-not $user -and $config) { $user = [string]$config.smtp_user }
if (-not $user) { $user = $to }
$pass = [string]$local.smtp_password
if (-not $to -or -not $pass) {
    Write-Host 'SMTP password or notify_email missing; cannot send fallback mail.'
    exit 1
}

$subject = '[cloud-agent-sync] 同期失敗（Python なし）'
$body = @"
cloud-agent-sync が起動できませんでした。

$Reason

Python 3 をインストールし、PATH を通してから再実行してください。
"@

$mail = New-Object System.Net.Mail.MailMessage
$mail.From = New-Object System.Net.Mail.MailAddress($user)
$mail.To.Add($to)
$mail.Subject = $subject
$mail.Body = $body
$mail.SubjectEncoding = [System.Text.Encoding]::UTF8
$mail.BodyEncoding = [System.Text.Encoding]::UTF8

$client = New-Object System.Net.Mail.SmtpClient($hostName, $port)
$client.EnableSsl = $true
$client.Credentials = New-Object System.Net.NetworkCredential($user, $pass)
$client.Timeout = 30000
try {
    $client.Send($mail)
    Write-Host ('Fallback notify sent: ' + $to)
} finally {
    $mail.Dispose()
    $client.Dispose()
}
