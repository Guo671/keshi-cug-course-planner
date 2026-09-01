$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw '未找到 .venv。请先在项目目录运行：py -3.12 -m venv .venv'
}

& $Python -m pip install -e "$ProjectRoot[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }

& $Python -m app.cli import-catalog
if ($LASTEXITCODE -ne 0) { throw '课程总库导入失败' }

Write-Host '初始化完成。运行 scripts\start.ps1 打开软件。' -ForegroundColor Green
