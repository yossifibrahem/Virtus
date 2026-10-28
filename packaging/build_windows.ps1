[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BuildDir = Join-Path $ProjectDir "build"
$DistDir = Join-Path $ProjectDir "dist"
$Version = if ($env:VIRTUS_BUILD_VERSION) { $env:VIRTUS_BUILD_VERSION } else { "0.1.0-alpha.1" }
$AppDir = Join-Path $ProjectDir "dist\Virtus AI Chat"
$Executable = Join-Path $AppDir "Virtus AI Chat.exe"
$Archive = Join-Path $ProjectDir "dist\Virtus-AI-Chat-$Version-windows-x64.zip"
$Checksum = "$Archive.sha256"
$SmokeRoot = Join-Path $BuildDir "windows-package-smoke"

Set-Location $ProjectDir

$Runtime = (python -c "import platform, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{platform.machine().lower()}')").Trim().Split('|')
if ($Runtime[0] -ne "3.12") {
    throw "The Windows package must be built with Python 3.12; found $($Runtime[0])."
}
if ($Runtime[1] -notin @("amd64", "x86_64")) {
    throw "The Windows x64 package requires an x64 Python runtime; found $($Runtime[1])."
}

python "packaging\write_build_metadata.py"
python "packaging\write_windows_icon.py"
python "packaging\write_windows_version.py"
python -m PyInstaller --clean --noconfirm "packaging\virtus_windows.spec"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "PyInstaller did not create $Executable"
}

Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Checksum -Force -ErrorAction SilentlyContinue
& tar.exe -a -cf $Archive -C $DistDir "Virtus AI Chat"
if ($LASTEXITCODE -ne 0) {
    throw "Could not create $Archive with the Windows libarchive tool."
}
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $(Split-Path -Leaf $Archive)" | Set-Content -LiteralPath $Checksum -Encoding ascii

$ResolvedBuild = [IO.Path]::GetFullPath($BuildDir)
$ResolvedSmoke = [IO.Path]::GetFullPath($SmokeRoot)
if (-not $ResolvedSmoke.StartsWith($ResolvedBuild + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use smoke directory outside the build root: $ResolvedSmoke"
}

try {
    Remove-Item -LiteralPath $SmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -LiteralPath $Archive -DestinationPath $SmokeRoot
    $ExtractedExecutable = Join-Path $SmokeRoot "Virtus AI Chat\Virtus AI Chat.exe"
    if (-not (Test-Path -LiteralPath $ExtractedExecutable -PathType Leaf)) {
        throw "The extracted onedir package is incomplete."
    }
    $env:VIRTUS_DESKTOP_SMOKE_TEST = "1"
    $env:VIRTUS_DESKTOP_NO_BROWSER = "1"
    $env:VIRTUS_DESKTOP_DATA_DIR = Join-Path $SmokeRoot "user-data"
    $Process = Start-Process -FilePath $ExtractedExecutable -Wait -PassThru -WindowStyle Hidden
    if ($Process.ExitCode -ne 0) {
        throw "The extracted package smoke test failed with exit code $($Process.ExitCode)."
    }
}
finally {
    Remove-Item Env:VIRTUS_DESKTOP_SMOKE_TEST -ErrorAction SilentlyContinue
    Remove-Item Env:VIRTUS_DESKTOP_NO_BROWSER -ErrorAction SilentlyContinue
    Remove-Item Env:VIRTUS_DESKTOP_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $SmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "Created $Archive"
