# ZYLO Desklet for Windows

[Download ZYLO-DESKLET.exe](https://github.com/UjanGuin/ZYLO-SYSTEM-COMMAND-DESKLET/releases/download/windows-v2.2/ZYLO-DESKLET.exe)

[Windows v2.2 release notes](https://github.com/UjanGuin/ZYLO-SYSTEM-COMMAND-DESKLET/releases/tag/windows-v2.2)

Windows v2.2 packages ZYLO 2.2.0 for Windows 11 x64. ZYLO is a desktop panel for asking questions and running everyday PowerShell tasks.

## Install or upgrade

1. Install [64-bit Python 3.11 or newer](https://www.python.org/downloads/windows/).
2. If ZYLO is running, quit it from the system tray.
3. Run `ZYLO-DESKLET.exe` and click **Install**. First-time dependency installation requires an internet connection. The installer reuses compatible ZYLO dependencies when available.
4. Open **ZYLO Desktop Assistant** from Start. The setup window lets you enter your name, choose a provider, and configure its connection. Existing settings are kept during an upgrade.

| Provider | Setup |
| --- | --- |
| NVIDIA NIM | Enter an NVIDIA API key for hosted Nemotron 3.5 Super completions. |
| Groq | Enter a Groq API key for hosted completions. |
| LM Studio | Start LM Studio's local server and set its base URL and model. The default endpoint is `http://127.0.0.1:1234/v1`. |

Add optional backup keys for NVIDIA and Groq. NVIDIA switches on a rate limit or request timeout. Groq retries on the active key for waits under 16 seconds and tries the backup for longer waits. NVIDIA retries HTTP 502 once on the current key. Groq retries rejected JSON generation once. Each prompt starts with its primary key.

A Tavily key is optional for web search. A blank API key field keeps the saved value. ZYLO stores settings and credentials locally under `%APPDATA%\ZYLO`. Keep the `step-desklet.env` file private.

## ZYLO for Windows v2.2

- Settings save immediately. Pasted keys receive a background validation check, and a verified Groq key starts the app-list build when app filtering is enabled.
- Exact Windows output volume commands are supported, including phrased requests such as "Set volume to 70%".
- Conversation history rolls forward and keeps the configured number of complete exchanges.
- ZYLO includes installed-app and file lookup, visible-window checks before app launches, alternate launchers, and a Gemini browser fallback.
- Short Groq rate limits are handled with automatic retries.

## Use the desklet

- Enter a prompt to ask a question or request a workstation action. The rim glows while ZYLO is working and stops moving when idle.
- Unlock the panel to move or resize it. Use the system tray to show or hide it, reopen settings, or quit.
- Press **Win+D** to show the desktop. The desklet returns when the wallpaper is exposed. Switching apps from the taskbar keeps it behind open windows.
- Open **Settings** from the tray or Start. **General** adjusts the 4–30 turn memory slider. **Providers** manages credentials. **Appearance** previews your colors, and **System** manages startup and approved tools.
- The circular-arrow button cancels the current request, stops its active command processes, and clears the panel.
- **Manage skills** under **System** lists PowerShell skills, lets you disable or approve them, and shows recent executions. Review a custom skill before trusting it.

### Administrator tools

**Allow requests for Windows administrator tools** is off by default. When enabled, ZYLO can ask to open Device Manager, Disk Management, Event Viewer, Services, Task Manager, or Windows Features. Approve the request in ZYLO, then respond to Windows User Account Control if prompted. Ordinary PowerShell requests run with ZYLO's current user privileges. ZYLO can make changes through those commands, so check the scope of the task you request.

## Build from source

From this directory in PowerShell, create an environment and install the pinned test dependencies.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-lock.txt
.\.venv\Scripts\python.exe -m unittest -v tests.test_desklet_ui tests.test_win32_prototypes tests.test_skills_security tests.test_worker_outcomes_and_shutdown
.\.venv\Scripts\python.exe zylo_win11.py
```

Build the per-user installer with the .NET Framework C# compiler included with Windows.

```powershell
.\packaging\Build-WindowsInstaller.ps1 -Python .\.venv\Scripts\python.exe
```

The installer is written to `dist\ZYLO-DESKLET.exe`. The packaged Python entry point is `zylo_win11.py`. The desktop window and tray live in `step_desklet/win11/`, the request worker and settings live in `step_desklet/core/`, and the setup dialog lives in `setup_app.py`.

The Windows x64 installer passed a smoke test and all 42 automated tests. Provider errors and administrator approvals were tested with simulated responses.

## License

[MIT](../LICENSE)
