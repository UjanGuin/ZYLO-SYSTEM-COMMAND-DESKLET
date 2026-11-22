# ZYLO Custom PowerShell Skills (`zylo_skills/`)

Drop any `.ps1` PowerShell script into this folder and **ZYLO** will automatically discover and expose it as an AI-callable tool on the next prompt (no restart required).

## Locations
- **Active User Folder (Windows 11)**: `%APPDATA%\ZYLO\zylo_skills\`
  - Quick shortcut: **Right-click ZYLO System Tray icon → Open Skills Folder (`zylo_skills/`)**
  - Any script placed here overrides a bundled script with the same skill ID.
- **Bundled Starter Skills**: Included automatically with ZYLO (`git_status_checker.ps1`, `media_controls.ps1`, `dev_shortcuts.ps1`).

## Optional Metadata Headers
Add up to 3 comment headers near the top of your `.ps1` file so the AI understands what your script does and what parameters it accepts:

```powershell
# @skill-name: project-cleanup
# @skill-description: Delete temporary build artifacts inside a target folder
# @skill-params: Path (directory path), DryRun (switch or true/false)

param(
    [string]$Path = ".",
    [switch]$DryRun
)

Write-Output "Scanning $Path (DryRun=$DryRun)"
```

- If `@skill-name` is omitted, ZYLO derives the ID from the filename (e.g., `my_tool.ps1` → `my-tool`).
- If `@skill-description` is omitted, ZYLO uses the first comment line in the file.
- Parameter names must be alphanumeric/underscore (`^[A-Za-z_][A-Za-z0-9_]*$`). String values are single-quoted safely by ZYLO before passing them to PowerShell (`pwsh.exe` / `powershell.exe` with `CREATE_NO_WINDOW`).

## Trust Allowlist, Disable Toggles & Audit History
Safe argument quoting does not make an arbitrary PowerShell script safe, so ZYLO enforces explicit local governance:

1. **SHA-256 Trust Allowlist**: Any custom `.ps1` you add (or any existing script you edit) is marked **Untrusted** until you explicitly approve its SHA-256 fingerprint via **System Tray → Manage Skills, Trust & History…**.
2. **Disable Individual Skills**: Uncheck any skill in the manager to block it from both AI prompts and execution (`%APPDATA%\ZYLO\skills-policy.json`).
3. **Execution History**: Every skill attempt (completed, failed, or blocked with `Command requires permission.`) is recorded in `%APPDATA%\ZYLO\skill-history.json`.

## Tips for Great Skills
1. Keep output concise plain text (`Write-Output`) — ZYLO summarizes up to `2,500` characters of stdout/stderr per step.
2. Prefer non-interactive flags and explicit defaults so skills return cleanly within the 120-second timeout.
3. Return a non-zero `exit 1` with a clear `Write-Error` message if a prerequisite (like `git` or `docker`) is missing.
