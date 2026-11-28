# liNi for Windows

[Download liNi 3.1](https://raw.githubusercontent.com/UjanGuin/ZYLO-SYSTEM-COMMAND-DESKLET/main/windows/downloads/liNi-Setup-Windows-x64.exe) · [Release notes](RELEASE-NOTES.md)

## Install or upgrade

1. Install [64-bit Python 3.11 or newer](https://www.python.org/downloads/windows/) on Windows 11 x64.
2. Quit the running desklet using its tray menu.
3. Open liNi-Setup-Windows-x64.exe and click **Install**. First-time dependency installation requires internet access. The installer is unsigned.
4. Search the Start menu for **liNi Desktop Assistant** or **liNi Chats**.

Upgrades carry forward your settings, API keys, conversations, skills, and any downloaded Whisper model. Existing profiles live under %APPDATA%\ZYLO and model files under %LOCALAPPDATA%\ZYLO\models.

## Providers

NVIDIA NIM and Groq are hosted providers. Add an API key for each provider you use. Each accepts one optional backup key. NVIDIA switches on rate limits or timeouts and retries HTTP 502 once when eligible. Groq waits on the current key for retry delays below four seconds and tries the backup for longer delays when available.

LM Studio and FreeLLMAPI can be enabled separately. LM Studio uses the default endpoint http://127.0.0.1:1234/v1. FreeLLMAPI uses the unified key, Auto or Fast routing, and http://127.0.0.1:31415/v1. Add a Tavily key for optional web search.

## Desklet and Chats

- Use the tray to show or hide the desklet, open Settings, or quit. Press Win+D to show the desktop. Unlock the desklet to move or resize it.
- The circular arrow cancels the active request and clears the panel. The gradient glow moves during work.
- **Settings → Appearance** has the **Editor's Choice** preset, custom gradients, and the Chats bubble color.
- **Settings → General** sets how many recent exchanges the assistant carries into its context. **liNi Chats** also keeps a local archive and supports search, formatted text, equations, and writing tools.
- Use **Assistant preferences** for custom instructions and optional weather coordinates. The assistant receives the PC's local time when building context.

## Voice

Open **Settings → Voice** and download Whisper Small (about 270 MB). Hold the configured shortcut while speaking and release to send. Change or disable the shortcut in Settings. Test your microphone and speaker there.

Spoken replies use Windows speech. Select an output device in Voice settings or use the Windows default. Click the reset icon on the desklet to stop playback and discard queued speech. Spoken replies are for voice input and can be turned off.

## Workstation tools

Ask for an installed application, a file by name, a volume percentage, or another supported task. liNi keeps an installed-app list that can be rebuilt from Settings, detects standard Windows user folders, and suggests similar filenames for confirmation. Search depth is configurable per folder.

Use the Skills manager to review enabled scripts, trust hashes, and execution history. Administrator tools require approval in liNi and can trigger Windows UAC.

## Build

From the windows directory, run .\packaging\Build-WindowsInstaller.ps1 in PowerShell. The build uses the Windows x64 .NET Framework C# compiler and writes dist\liNi-Setup-Windows-x64.exe. See requirements-dev.txt for development dependencies and tests for offline checks.

The bundled equation renderer is MathJax. The downloads page publishes the installer checksum. liNi verifies voice model downloads before using them.
