# @skill-name: installed-apps
# @skill-description: Read the Windows installed app catalog, including Start menu, Store, installer, and App Paths entries
# @skill-params: Name (optional app name; omit to list installed applications)

param([string]$Name = "")

$ErrorActionPreference = "Stop"
$apps = [System.Collections.Generic.List[object]]::new()

function Add-App([string]$DisplayName, [string]$Kind, [string]$Target = "", [string]$WindowExecutable = "") {
    if ([string]::IsNullOrWhiteSpace($DisplayName)) { return }
    $apps.Add([pscustomobject]@{
        Name = $DisplayName.Trim()
        Kind = $Kind
        Target = $Target
        WindowExecutable = $WindowExecutable
    })
}

try { $shortcutReader = New-Object -ComObject WScript.Shell } catch { $shortcutReader = $null }
$folders = @(
    @{ Path = [Environment]::GetFolderPath("Programs"); Recurse = $true }
    @{ Path = [Environment]::GetFolderPath("CommonPrograms"); Recurse = $true }
    @{ Path = [Environment]::GetFolderPath("DesktopDirectory"); Recurse = $false }
    @{ Path = [Environment]::GetFolderPath("CommonDesktopDirectory"); Recurse = $false }
    @{ Path = (Join-Path $env:USERPROFILE "OneDrive\Desktop"); Recurse = $false }
)
foreach ($folder in $folders) {
    $directory = $folder.Path
    if (-not $directory) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $directory -File -Recurse:$folder.Recurse -ErrorAction SilentlyContinue) {
        if ($file.Extension -eq ".appref-ms") {
            Add-App $file.BaseName "shortcut" $file.FullName
        } elseif ($file.Extension -eq ".lnk" -and $shortcutReader) {
            try {
                $shortcut = $shortcutReader.CreateShortcut($file.FullName)
                $target = $shortcut.TargetPath
                if ($target -match "^[A-Za-z]:\\.+\.exe$" -and (Test-Path -LiteralPath $target -PathType Leaf)) {
                    # A browser-hosted app shares its executable with unrelated windows.
                    $windowExecutable = $target
                    if ($shortcut.Arguments -match '(?i)(^|\s)--app(?:-id)?(?:=|\s)') { $windowExecutable = "" }
                    Add-App $file.BaseName "shortcut" $file.FullName $windowExecutable
                }
            } catch {
                # Skip malformed shortcuts.
            }
        }
    }
}

$startApps = @()
try {
    $startApps = @(Get-StartApps -ErrorAction Stop)
} catch {
    # PowerShell editions without the StartLayout cmdlet can use the same shell namespace.
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.NameSpace("shell:AppsFolder")
        if ($folder) {
            $startApps = @($folder.Items() | ForEach-Object {
                [pscustomobject]@{Name = $_.Name; AppID = $_.Path}
            })
        }
    } catch {
        # Registry and classic shortcuts can still be read without the shell.
    }
}
foreach ($app in $startApps) {
    Add-App $app.Name "apps-folder" $app.AppID
}

foreach ($hive in @("HKCU:", "HKLM:")) {
    foreach ($base in @("SOFTWARE", "SOFTWARE\WOW6432Node")) {
        $currentVersion = "$hive\$base\Microsoft\Windows\CurrentVersion"
        foreach ($key in Get-ChildItem -LiteralPath "$currentVersion\App Paths" -ErrorAction SilentlyContinue) {
            $target = [Environment]::ExpandEnvironmentVariables([string]$key.GetValue("")).Trim('"')
            if ($target -match "^[A-Za-z]:\\.+\.exe$" -and (Test-Path -LiteralPath $target -PathType Leaf)) {
                Add-App ([IO.Path]::GetFileNameWithoutExtension($key.PSChildName)) "executable" $target
            }
        }
        foreach ($key in Get-ChildItem -LiteralPath "$currentVersion\Uninstall" -ErrorAction SilentlyContinue) {
            $entry = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if (-not $entry.DisplayName -or $entry.SystemComponent -eq 1 -or $entry.ReleaseType -match "Update|Hotfix") {
                continue
            }
            Add-App $entry.DisplayName "installed"
            # Some installers omit a Start shortcut. Accept an icon executable only
            # when its name agrees with the product, never an uninstall command.
            $target = [Environment]::ExpandEnvironmentVariables([string]$entry.DisplayIcon)
            $target = ($target -replace ",-?\d+\s*$", "").Trim().Trim('"')
            if ($target -notmatch "^[A-Za-z]:\\.+\.exe$" -or -not (Test-Path -LiteralPath $target -PathType Leaf)) {
                continue
            }
            $title = $entry.DisplayName -replace "\s+v?\d+(\.\d+)+.*$", ""
            $exe = [IO.Path]::GetFileNameWithoutExtension($target)
            $title = $title -replace "\s+(?:desktop|app|application)$", ""
            if ($title -eq "VLC media player") { $title = "VLC" }
            $exe = $exe -replace "[-_ ](?:desktop|app|application)$", ""
            $titleKey = $title -replace "[^\p{L}\p{N}]", ""
            $exeKey = $exe -replace "[^\p{L}\p{N}]", ""
            if ($exeKey.Length -ge 3 -and $titleKey -eq $exeKey -and $exeKey -notmatch "^(?:update|uninstall|setup|install|repair)") {
                Add-App $entry.DisplayName "executable" $target
            }
        }
    }
}

$entries = $apps.ToArray()
if ($Name.Trim()) {
    $query = ($Name -replace "[^\p{L}\p{N}]", "").ToLowerInvariant()
    $entries = @($entries | Where-Object {
        $query -and ($_.Name -replace "[^\p{L}\p{N}]", "").ToLowerInvariant().Contains($query)
    } | Select-Object -First 20)
}

ConvertTo-Json -InputObject $entries -Depth 3 -Compress
