# @skill-name: git-status-checker
# @skill-description: Inspect git branch, working tree status, and last 5 commits in a repo folder
# @skill-params: Path (optional directory path, defaults to current working directory)

param(
    [string]$Path = "."
)

$ErrorActionPreference = "Stop"
$Target = Resolve-Path -LiteralPath $Path -ErrorAction Stop

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git is not installed or not in PATH."
    exit 1
}

Push-Location $Target
try {
    $InsideRepo = git rev-parse --is-inside-work-tree 2>$null
    if ($InsideRepo -ne "true") {
        Write-Output "Not a git repository: $Target"
        exit 0
    }

    $Branch = git branch --show-current
    $Status = git status --short
    $Recent = git log -n 5 --oneline

    Write-Output "Repo: $Target"
    Write-Output "Branch: $Branch"
    Write-Output "--- Status ---"
    if ($Status) { $Status } else { Write-Output "(clean working tree)" }
    Write-Output "--- Recent Commits ---"
    $Recent
} finally {
    Pop-Location
}
