[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$buildDependencies = Join-Path $projectRoot ".build_deps"
$requirementsFile = Join-Path $projectRoot "requirements-build.txt"
$entryPoint = Join-Path $projectRoot "launch_eis_gui.pyw"
$versionFile = Join-Path $projectRoot "packaging\version_info.txt"
$iconFile = Join-Path $projectRoot "packaging\app_icon.ico"
$distDirectory = Join-Path $projectRoot "dist"
$workDirectory = Join-Path $projectRoot "build\work"
$specDirectory = Join-Path $projectRoot "build"

if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
    throw "Application icon is missing: $iconFile"
}

[System.IO.Directory]::CreateDirectory($buildDependencies) | Out-Null
$existingPythonPath = $env:PYTHONPATH
if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
    $env:PYTHONPATH = "$buildDependencies;$projectRoot"
}
else {
    $env:PYTHONPATH = "$buildDependencies;$projectRoot;$existingPythonPath"
}

& $PythonExe -c "import PyInstaller, originpro, OriginExt, PIL"
if ($LASTEXITCODE -ne 0) {
    & $PythonExe -m pip install --disable-pip-version-check --upgrade `
        --target $buildDependencies `
        -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install the local build dependencies."
    }
}

Push-Location $projectRoot
try {
    & $PythonExe -m unittest discover -s tests -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Unit tests failed; the executable was not built."
    }

    $arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "Origin-EIS-Plotter",
        "--icon", $iconFile,
        "--paths", $projectRoot,
        "--hidden-import", "OriginExt",
        "--collect-all", "originpro",
        "--collect-all", "OriginExt",
        "--copy-metadata", "originpro",
        "--copy-metadata", "OriginExt",
        "--add-data", "$iconFile;app_resources",
        "--version-file", $versionFile,
        "--distpath", $distDirectory,
        "--workpath", $workDirectory,
        "--specpath", $specDirectory,
        $entryPoint
    )
    & $PythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $existingPythonPath
}

$executable = Join-Path $distDirectory "Origin-EIS-Plotter.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The expected executable was not created: $executable"
}

$hash = Get-FileHash -LiteralPath $executable -Algorithm SHA256
[ordered]@{
    status = "ok"
    executable = $executable
    bytes = (Get-Item -LiteralPath $executable).Length
    sha256 = $hash.Hash
} | ConvertTo-Json -Compress
