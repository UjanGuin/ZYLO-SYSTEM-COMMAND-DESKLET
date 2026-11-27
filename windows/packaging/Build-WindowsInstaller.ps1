param(
    [string]$Python = "python",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $project "dist"
}
$output = New-Item -ItemType Directory -Force -Path $OutputDirectory
$installer = Join-Path $output.FullName "liNi-Setup-Windows-x64.exe"
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "The 64-bit .NET Framework C# compiler is required."
}

$temp = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ("zylo-build-" + [guid]::NewGuid().ToString("N")))
$payload = Join-Path $temp.FullName "payload.zip"
try {
    @'
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])
files = [root / name for name in ("zylo_win11.py", "setup_app.py", "requirements.txt", "requirements-lock.txt")]
files.append(root / "packaging" / "zylo.ico")
files += sorted((root / "step_desklet").glob("*/*.py"))
files += sorted((root / "step_desklet" / "win11" / "assets").glob("*.*"))
files += sorted((root / "zylo_skills").glob("*.ps1"))
with ZipFile(sys.argv[2], "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
    for file in files:
        name = file.relative_to(root).as_posix()
        if file.suffix == ".py":
            compile(file.read_bytes(), name, "exec")
        archive.write(file, name)
'@ | & $Python -B - $project $payload
    if ($LASTEXITCODE -ne 0) {
        throw "Could not build the embedded application package."
    }

    $icon = Join-Path $PSScriptRoot "zylo.ico"
    $source = Join-Path $PSScriptRoot "ZyloSetup.cs"
    & $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$installer" "/win32icon:$icon" "/resource:$payload,ZyloPayload.zip" /r:System.IO.Compression.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:Microsoft.CSharp.dll $source
    if ($LASTEXITCODE -ne 0) {
        throw "Could not compile the Windows installer."
    }

    Get-FileHash -LiteralPath $installer -Algorithm SHA256
}
finally {
    if (Test-Path -LiteralPath $payload) {
        Remove-Item -LiteralPath $payload
    }
    Remove-Item -LiteralPath $temp.FullName
}
