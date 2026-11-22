# @skill-name: dev-shortcuts
# @skill-description: Check active developer localhost ports (3000, 5173, 8000, 8080, 1234) and local SDK versions
# @skill-params: Mode (ports | versions | all, defaults to all)

param(
    [ValidateSet("ports", "versions", "all")]
    [string]$Mode = "all"
)

if ($Mode -eq "ports" -or $Mode -eq "all") {
    $TargetPorts = @(1234, 3000, 5000, 5173, 8000, 8080)
    $Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $TargetPorts -contains $_.LocalPort } |
        Select-Object LocalPort, OwningProcess |
        Sort-Object LocalPort -Unique

    Write-Output "--- Active Dev Ports ---"
    if ($Listeners) {
        foreach ($item in $Listeners) {
            $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue
            $pname = if ($proc) { $proc.ProcessName } else { "unknown" }
            Write-Output "Port $($item.LocalPort): $pname (PID $($item.OwningProcess))"
        }
    } else {
        Write-Output "No common dev ports listening (1234, 3000, 5000, 5173, 8000, 8080)."
    }
}

if ($Mode -eq "versions" -or $Mode -eq "all") {
    Write-Output "--- Dev Toolchain ---"
    foreach ($cmd in @("pwsh", "python", "node", "git")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            $ver = (& $cmd --version 2>$null | Select-Object -First 1)
            Write-Output "${cmd}: $ver"
        } else {
            Write-Output "${cmd}: not found"
        }
    }
}
