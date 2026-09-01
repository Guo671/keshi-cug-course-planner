[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$DesktopBuildRoot = Join-Path $ProjectRoot 'build\desktop'
$DistRoot = Join-Path $DesktopBuildRoot 'dist'
$WorkRoot = Join-Path $DesktopBuildRoot 'work'
$SeedDatabase = Join-Path $DesktopBuildRoot 'seed\planner.db'
# Windows PowerShell 5.1 reads BOM-less scripts as the active ANSI code page.
# Build the Chinese product name from Unicode code points so this script remains ASCII-safe.
$ProductName = [string][char]0x8BFE + [string][char]0x77F3
$BundleName = "$ProductName-v0.2.0-win64"
$BundleDir = Join-Path $DistRoot $BundleName
$ReleaseDir = Join-Path $ProjectRoot 'release'
$ZipPath = Join-Path $ReleaseDir "$BundleName.zip"
$SmokeRoot = $null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Remove-ValidatedProjectDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolvedProject = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    $resolvedTarget = [IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
    if (-not $resolvedTarget.StartsWith($resolvedProject, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the project: $resolvedTarget"
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Desktop bundles must be built on Windows.'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv\Scripts\python.exe. Create the project Python 3.12 environment first.'
}

$PythonFacts = & $Python -c "import json, platform, struct, sys; print(json.dumps({'version': list(sys.version_info[:2]), 'bits': struct.calcsize('P') * 8, 'machine': platform.machine()}))"
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the build Python.' }
$Facts = $PythonFacts | ConvertFrom-Json
if ($Facts.version[0] -ne 3 -or $Facts.version[1] -ne 12 -or $Facts.bits -ne 64) {
    throw "Expected 64-bit Python 3.12; got $PythonFacts"
}

Push-Location $ProjectRoot
try {
    if (-not $SkipInstall) {
        Invoke-Checked 'Installing desktop build dependencies' {
            & $Python -m pip install -e "$ProjectRoot[dev,desktop,build]"
        }
    }

    Remove-ValidatedProjectDirectory $DesktopBuildRoot
    New-Item -ItemType Directory -Path (Split-Path -Parent $SeedDatabase) -Force | Out-Null

    Invoke-Checked 'Generating application icon' {
        & $Python (Join-Path $ProjectRoot 'desktop\tools\generate_icon.py')
    }
    Invoke-Checked 'Preparing privacy-checked seed database' {
        & $Python (Join-Path $ProjectRoot 'desktop\tools\prepare_seed.py') `
            (Join-Path $ProjectRoot 'var\planner.db') $SeedDatabase
    }

    if (-not $SkipTests) {
        Invoke-Checked 'Running Python tests' {
            & $Python -m pytest
        }
    }

    $env:KESHI_BUILD_SEED_DB = $SeedDatabase
    Invoke-Checked 'Building the PyInstaller one-folder bundle' {
        & $Python -m PyInstaller --noconfirm --clean `
            --distpath $DistRoot --workpath $WorkRoot `
            (Join-Path $ProjectRoot 'desktop\CUGCoursePlanner.spec')
    }
    Remove-Item Env:KESHI_BUILD_SEED_DB -ErrorAction SilentlyContinue

    if (-not (Test-Path -LiteralPath $BundleDir -PathType Container)) {
        throw "PyInstaller did not create the expected bundle: $BundleDir"
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'desktop\PORTABLE_README.txt') `
        -Destination (Join-Path $BundleDir 'README.txt') -Force
    foreach ($NoticeFile in @('LICENSE', 'DATA_NOTICE.md', 'THIRD_PARTY_NOTICES.md')) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $NoticeFile) `
            -Destination (Join-Path $BundleDir $NoticeFile) -Force
    }

    $ExePath = Join-Path $BundleDir 'Keshi.exe'
    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        throw "Missing frozen executable: $ExePath"
    }
    $VersionOutput = & $ExePath --version
    if ($LASTEXITCODE -ne 0 -or ($VersionOutput -join '').Trim() -ne 'Keshi 0.2.0') {
        throw "Frozen version check failed: $VersionOutput"
    }

    $SmokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("keshi-frozen-smoke-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
    $SmokeOutput = & $ExePath --smoke-test --data-dir $SmokeRoot
    if ($LASTEXITCODE -ne 0) { throw 'Frozen smoke test failed.' }
    $SmokeResult = ($SmokeOutput -join "`n") | ConvertFrom-Json
    if ($SmokeResult.status -ne 'ok' -or -not $SmokeResult.frozen -or $SmokeResult.health.status -ne 'ok') {
        throw "Frozen smoke test returned an invalid result: $SmokeOutput"
    }

    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
    Compress-Archive -LiteralPath $BundleDir -DestinationPath $ZipPath -CompressionLevel Optimal
    $Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumPath = Join-Path $ReleaseDir 'SHA256SUMS.txt'
    $ChecksumLine = "$Hash  $BundleName.zip`n"
    [IO.File]::WriteAllText(
        $ChecksumPath,
        $ChecksumLine,
        (New-Object Text.UTF8Encoding($false))
    )

    Write-Host "Desktop bundle verified: $BundleDir" -ForegroundColor Green
    Write-Host "Portable ZIP: $ZipPath" -ForegroundColor Green
    Write-Host "SHA256: $Hash" -ForegroundColor Green
}
finally {
    Remove-Item Env:KESHI_BUILD_SEED_DB -ErrorAction SilentlyContinue
    if ($null -ne $SmokeRoot -and (Test-Path -LiteralPath $SmokeRoot)) {
        $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        $resolvedSmoke = [IO.Path]::GetFullPath($SmokeRoot).TrimEnd('\') + '\'
        if ($resolvedSmoke.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $SmokeRoot).StartsWith('keshi-frozen-smoke-')) {
            Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
        }
    }
    Pop-Location
}
