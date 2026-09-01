$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw '未找到项目虚拟环境，请先运行 scripts\setup.ps1'
}

Set-Location -LiteralPath $ProjectRoot
Write-Host '地大课表正在运行：http://127.0.0.1:8765' -ForegroundColor Cyan
& $Python -m app.cli serve --host 127.0.0.1 --port 8765
