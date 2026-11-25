# @skill-name: find-file
# @skill-description: Find exact or similar filenames in the configured Windows and OneDrive folders
# @skill-params: Name (file name, e.g. mayarkhela.pdf; omit the path)

param([Parameter(Mandatory = $true)][string]$Name)

$ErrorActionPreference = "Stop"
$Name = $Name.Trim()
if (-not $Name -or $Name.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw "Supply a file name without a directory."
}
function Get-RegisteredFolderPath([string]$Folder) {
    if ($Folder -ne 'Downloads') {
        return [Environment]::GetFolderPath([Environment+SpecialFolder]$Folder)
    }

    # Downloads has a Windows Known Folder ID but no .NET SpecialFolder value.
    $downloadsId = '{374DE290-123F-4565-9164-39C4925E467B}'
    $shellFolders = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $registered = Get-ItemPropertyValue -LiteralPath $shellFolders -Name $downloadsId -ErrorAction SilentlyContinue
    if ($registered) { return [Environment]::ExpandEnvironmentVariables($registered) }

    try {
        $shell = New-Object -ComObject Shell.Application
        $downloads = $shell.NameSpace('shell:Downloads')
        if ($downloads) { return $downloads.Self.Path }
    } catch {
        return $null
    }
}

function Get-FolderLevel([string]$Path) {
    # The drive root is level 1. Files do not add a folder level.
    if ($Path -notmatch '^[a-zA-Z]:\\') { return 0 }
    return 1 + @($Path.Substring(3) -split '\\' | Where-Object { $_ }).Count
}

function Test-SearchRoot([string]$Path) {
    # Check each component before traversing it. OneDrive placeholders are not directory links.
    $current = [IO.Path]::GetPathRoot($Path)
    foreach ($part in ($Path.Substring($current.Length) -split '\\' | Where-Object { $_ })) {
        $current = Join-Path $current $part
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if (-not $item.PSIsContainer -or $item.LinkType -in ('Junction', 'SymbolicLink')) { return $false }
    }
    return $true
}

function Get-SavedFolderLevels {
    $appData = $env:APPDATA
    if (-not $appData) { $appData = [Environment]::GetFolderPath('ApplicationData') }
    if (-not $appData) { return $null }
    $settingsFile = Join-Path $appData 'ZYLO\settings.json'
    try {
        $settings = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        return $settings.fileSearchLevels
    } catch {
        return $null
    }
}

$savedLevels = Get-SavedFolderLevels
$folderPolicies = @(
    @{ Folder = 'MyVideos'; Key = 'videos'; DefaultLevel = 6 }
    @{ Folder = 'MyPictures'; Key = 'pictures'; DefaultLevel = 6 }
    @{ Folder = 'MyMusic'; Key = 'music'; DefaultLevel = 6 }
    @{ Folder = 'MyDocuments'; Key = 'documents'; DefaultLevel = 7 }
    @{ Folder = 'Downloads'; Key = 'downloads'; DefaultLevel = 7 }
    @{ Folder = 'DesktopDirectory'; Key = 'desktop'; DefaultLevel = 7 }
)
$exact = [System.Collections.Generic.List[string]]::new()
$candidates = [System.Collections.Generic.List[object]]::new()
$scanErrors = @()
$unavailableRoot = $false
$extension = [IO.Path]::GetExtension($Name)
$queryStem = [IO.Path]::GetFileNameWithoutExtension($Name)
if (-not $extension -and $Name -match '^(.+?)\s+(pdf|txt|text|md|doc|docx|csv|json|ya?ml)$') {
    # Keep the original name for exact matches, but accept a spoken extension for suggestions.
    $queryStem = $Matches[1]
    $fileType = $Matches[2].ToLowerInvariant()
    $extension = if ($fileType -eq 'text') { '.txt' } else { ".$fileType" }
}

function Get-NameWords([string]$Text) {
    $Text.Normalize().ToLowerInvariant() -split '[^\p{L}\p{M}\p{Nd}]+' | Where-Object { $_ }
}

$words = @(Get-NameWords $queryStem)
$stem = $words -join ''

function Get-TypoDistance([string]$Left, [string]$Right, [int]$Limit) {
    # Only visit cells close enough to the diagonal to be within the edit limit.
    $tooFar = $Limit + 1
    $previous = [int[]]::new($Right.Length + 1)
    $current = [int[]]::new($Right.Length + 1)
    $older = [int[]]::new($Right.Length + 1)
    for ($j = 0; $j -le [Math]::Min($Right.Length, $Limit + 1); $j++) { $previous[$j] = $j }
    for ($i = 1; $i -le $Left.Length; $i++) {
        $first = [Math]::Max(1, $i - $Limit)
        $last = [Math]::Min($Right.Length, $i + $Limit)
        $current[$first - 1] = if ($first -eq 1) { $i } else { $tooFar }
        if ($last -lt $Right.Length) { $current[$last + 1] = $tooFar }
        for ($j = $first; $j -le $last; $j++) {
            $cost = [int]($Left[$i - 1] -cne $Right[$j - 1])
            $distance = [Math]::Min(1 + [Math]::Min($current[$j - 1], $previous[$j]), $previous[$j - 1] + $cost)
            if ($i -gt 1 -and $j -gt 1 -and $Left[$i - 1] -ceq $Right[$j - 2] -and $Left[$i - 2] -ceq $Right[$j - 1]) {
                $distance = [Math]::Min($distance, $older[$j - 2] + 1)
            }
            $current[$j] = $distance
        }
        $reuse = $older
        $older = $previous
        $previous = $current
        $current = $reuse
    }
    return $previous[$Right.Length]
}

function Get-NamePartDistance([string]$Query, [string[]]$NameWords, [int]$Limit) {
    $nearest = $Limit + 1
    $minimum = $Query.Length - $Limit
    $maximum = $Query.Length + $Limit
    for ($start = 0; $start -lt $NameWords.Count; $start++) {
        $part = ''
        for ($end = $start; $end -lt $NameWords.Count; $end++) {
            $part += $NameWords[$end]
            if ($part.Length -gt $maximum) { break }
            if ($part.Length -lt $minimum) { continue }
            if ($start -eq 0 -and $end -eq ($NameWords.Count - 1)) { break }
            $distance = Get-TypoDistance $Query $part $Limit
            $nearest = [Math]::Min($nearest, $distance)
            if ($nearest -le 1) { return $nearest }
        }
    }
    return $nearest
}

function Test-LeadingAcronym([string[]]$QueryWords, [string[]]$NameWords) {
    $initials = $QueryWords[0]
    $requiredWords = $initials.Length + $QueryWords.Count - 1
    for ($start = 0; ($start + $requiredWords) -le $NameWords.Count; $start++) {
        $index = 0
        while ($index -lt $initials.Length -and $NameWords[$start + $index][0] -ceq $initials[$index]) {
            $index++
        }
        if ($index -ne $initials.Length) { continue }

        $tail = $start + $initials.Length - 1
        $index = 1
        while ($index -lt $QueryWords.Count -and $NameWords[$tail + $index] -ceq $QueryWords[$index]) {
            $index++
        }
        if ($index -eq $QueryWords.Count) { return $true }
    }
    return $false
}

$locations = @{}
foreach ($policy in $folderPolicies) {
    $registeredPath = Get-RegisteredFolderPath $policy.Folder
    if (-not $registeredPath -or $registeredPath -notmatch '^[a-zA-Z]:\\') {
        $unavailableRoot = $true
        continue
    }
    $root = [IO.Path]::GetFullPath($registeredPath)
    $level = Get-FolderLevel $root
    $maxLevel = $policy.DefaultLevel
    $setting = if ($savedLevels) { $savedLevels.PSObject.Properties[$policy.Key] }
    if ($setting) {
        $value = $setting.Value
        if (($value -is [int] -or $value -is [long]) -and $value -ge 0 -and $value -le [int]::MaxValue) {
            $maxLevel = [int]$value
        }
    }
    if ($level -lt 2 -or ($maxLevel -gt 0 -and $level -gt $maxLevel)) { continue }
    if (-not (Test-SearchRoot $root)) {
        $unavailableRoot = $true
        continue
    }
    $root = $root.TrimEnd('\')
    if ($locations.ContainsKey($root)) {
        $otherLimit = $locations[$root].MaxLevel
        if ($otherLimit -gt 0 -and ($maxLevel -eq 0 -or $otherLimit -lt $maxLevel)) {
            $maxLevel = $otherLimit
        }
    }
    $locations[$root] = @{ Level = $level; MaxLevel = $maxLevel }
}

foreach ($location in $locations.GetEnumerator()) {
    $pending = [System.Collections.Generic.Stack[object]]::new()
    $pending.Push(@{ Path = $location.Key; Level = $location.Value.Level })
    while ($pending.Count) {
        $directory = $pending.Pop()
        $canDescend = $location.Value.MaxLevel -eq 0 -or $directory.Level -lt $location.Value.MaxLevel
        foreach ($item in Get-ChildItem -LiteralPath $directory.Path -Force -ErrorAction SilentlyContinue -ErrorVariable +scanErrors) {
            if ($item.PSIsContainer) {
                if ($canDescend -and $item.LinkType -notin ("Junction", "SymbolicLink")) {
                    $pending.Push(@{ Path = $item.FullName; Level = $directory.Level + 1 })
                }
                continue
            }
            if ([string]::Equals($item.Name, $Name, [StringComparison]::OrdinalIgnoreCase)) {
                $exact.Add($item.FullName)
            } elseif ($stem -and (-not $extension -or $item.Extension -eq $extension)) {
                $candidates.Add($item)
            }
        }
    }
}

$paths = if ($exact.Count) {
    $exact | Sort-Object -Unique
} else {
    $limit = if ($stem.Length -ge 14) { 3 } elseif ($stem.Length -ge 8) { 2 } else { 1 }
    $hasAcronym = $words.Count -ge 2 -and $words[0] -match '^\p{L}{2,4}$'
    $ranked = foreach ($item in $candidates) {
        $candidateWords = @(Get-NameWords $item.BaseName)
        $candidate = $candidateWords -join ''
        $difference = [Math]::Abs($stem.Length - $candidate.Length)
        $score = $null
        if ($candidate -ceq $stem) {
            $score = 0
        } elseif ($hasAcronym -and (Test-LeadingAcronym $words $candidateWords)) {
            $score = 5
        } elseif ($stem.Length -ge 4 -and $candidate.Contains($stem)) {
            $score = 20 + $difference
        } elseif ($words.Count -ge 2 -and $stem.Length -ge 5 -and
                  @($words | Where-Object { $_ -cnotin $candidateWords }).Count -eq 0) {
            $score = 30 + $difference
        } elseif ($stem.Length -ge 3 -and $candidate.Length -ge 3 -and $difference -le $limit) {
            $distance = Get-TypoDistance $stem $candidate $limit
            if ($distance -le $limit) { $score = 10 + $distance }
        }
        if ($null -eq $score) {
            if ($stem.Length -lt 5 -or $candidateWords.Count -lt 2) { continue }
            $distance = Get-NamePartDistance $stem $candidateWords $limit
            if ($distance -gt $limit) { continue }
            $score = 40 + $distance * 4 + [Math]::Min($difference, 20)
        }
        [pscustomobject]@{ Score = $score; Path = $item.FullName }
    }
    $ranked | Sort-Object -Property Score, Path -Unique | Select-Object -ExpandProperty Path
}
$paths = @($paths | Where-Object { $_ })
ConvertTo-Json -Compress -Depth 3 -InputObject ([pscustomobject]@{
    Count = $paths.Count
    Paths = @($paths | Select-Object -First 10)
    Incomplete = $unavailableRoot -or ($scanErrors.Count -gt 0)
})
