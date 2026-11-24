# @skill-name: find-file
# @skill-description: Find a file by name in the configured Windows and OneDrive user folders, with fixed depth limits
# @skill-params: Name (file name, e.g. mayarkhela.pdf; omit the path)

param([Parameter(Mandatory = $true)][string]$Name)

$ErrorActionPreference = "Stop"
$Name = $Name.Trim()
if (-not $Name -or $Name.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw "Supply a file name without a directory."
}
if (-not $env:USERPROFILE) {
    throw "The Windows user profile path is unavailable."
}

# Each limit counts folders below the configured root, not the file itself.
$locations = @(
    @{ Path = "Videos"; Below = 1 }
    @{ Path = "OneDrive\Pictures"; Below = 1 }
    @{ Path = "OneDrive\Music"; Below = 1 }
    @{ Path = "OneDrive\Documents"; Below = 2 }
    @{ Path = "Downloads"; Below = 2 }
    @{ Path = "OneDrive\Desktop"; Below = 2 }
)
$exact = [System.Collections.Generic.List[string]]::new()
$similar = [System.Collections.Generic.List[string]]::new()
$extension = [IO.Path]::GetExtension($Name)
$stem = ([IO.Path]::GetFileNameWithoutExtension($Name) -replace "[\s_\-]", "").ToLowerInvariant()

foreach ($location in $locations) {
    $root = Join-Path $env:USERPROFILE $location.Path
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
    $pending = [System.Collections.Generic.Stack[object]]::new()
    $pending.Push(@{ Path = $root; Below = $location.Below })
    while ($pending.Count) {
        $directory = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory.Path -ErrorAction SilentlyContinue) {
            if ($item.PSIsContainer) {
                if ($directory.Below -gt 0 -and $item.LinkType -notin ("Junction", "SymbolicLink")) {
                    $pending.Push(@{ Path = $item.FullName; Below = $directory.Below - 1 })
                }
                continue
            }
            if ($item.Name -eq $Name) {
                $exact.Add($item.FullName)
            } elseif ($stem -and (-not $extension -or $item.Extension -eq $extension)) {
                $candidate = ($item.BaseName -replace "[\s_\-]", "").ToLowerInvariant()
                if ($candidate -eq $stem) { $similar.Add($item.FullName) }
            }
        }
    }
}

$found = if ($exact.Count) { $exact } else { $similar }
$paths = @($found | Sort-Object -Unique)
ConvertTo-Json -Compress -Depth 3 -InputObject ([pscustomobject]@{
    Count = $paths.Count
    Paths = @($paths | Select-Object -First 10)
})
