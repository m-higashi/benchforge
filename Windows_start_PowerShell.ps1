# Benchforge 起動 (Windows PowerShell 版・UTF-8ネイティブで日本語が化けない代替)
# 使い方: PowerShell で  ./start.ps1   （Windows_start.bat が化ける環境向けの代替）
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location -Path $PSScriptRoot

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "仮想環境がありません。先に Windows_setup.bat を実行してください。" -ForegroundColor Red
  Read-Host "Enterキーで終了"
  exit 1
}
if (-not (Test-Path "config.yaml")) { Copy-Item "config.example.yaml" "config.yaml" }

Write-Host "Benchforge を起動します…"
& $py run.py @args
