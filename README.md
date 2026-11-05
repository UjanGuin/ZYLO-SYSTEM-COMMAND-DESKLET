# ZYLO Desktop Assistant

ZYLO is a small GNOME Shell desktop assistant for Fedora Workstation. The assistant is a desktop-only Shell extension; the launcher is a separate setup window used to enter a display name, configure provider keys, and choose an accent color.

## Current features

- Native GNOME Shell extension with a movable, resizable, lockable glass-style desktop panel.
- NVIDIA NIM using `stepfun-ai/step-5-preview` by default, and Groq using `openai/gpt-oss-120b` as an alternate provider.
- Short conversation history persisted locally between requests and logins.
- Optional Tavily search. The assistant can request current web information; search results are passed back as untrusted reference material.
- Setup launcher stores provider credentials in `~/.config/step-desklet.env` with private file permissions. Keys are not part of the source tree.
- Accent color selection in setup, applied to the extension's accent details while preserving the dark panel and established send-button style.
- Response text clears after a delay based on response length.
- No voice input or speech output.

## Fedora installation

Requirements: Fedora Workstation, GNOME Shell 51 (the version declared in the extension metadata), Python 3, and network access for installing Python packages and calling the configured services.

From the project directory:

```bash
chmod +x install.sh install-extension.sh
./install.sh
```

Open **ZYLO Desktop Assistant** from the Applications grid or run `./step-desklet` to open setup. Enter your name, add the provider keys you plan to use, choose an accent, and save. The launcher links to the NVIDIA, Groq, and Tavily key pages. If GNOME Shell does not load the newly installed extension in the current session, log out and back in once.

The setup launcher can be opened again at any time to change the name, keys, or accent color. Leaving a key field blank keeps the saved key.

## Using the desktop panel

- Drag the header to move the panel while it is unlocked.
- Click the lock icon to lock its position; unlock it to move or resize.
- Use the reload/clear icon to clear the current response.
- Click the provider icon to switch between NVIDIA NIM and Groq.
- Enter a prompt and press Enter or the arrow button. The status area reports thinking, web search, or command execution while the assistant works.
- The panel is shown on the desktop and hidden while another application is focused or GNOME Overview is open.

## Providers and credentials

Credentials are kept in `~/.config/step-desklet.env` (mode `0600`):

```dotenv
NVIDIA_API_KEY=...
GROQ_API_KEY=...
TAVILY_API_KEY=...
```

The setup application writes these values locally and never adds them to Git. NVIDIA and Groq are the chat providers. Tavily is only called when the assistant chooses the `search` action and a Tavily key is configured. A missing Tavily key produces an explanatory error if a search is requested.

## Local state

- Setup preferences: `~/.config/step-desklet/settings.json`
- Panel position, size, and lock state: `~/.config/step-desklet/layout.json`
- Recent conversation turns: `~/.config/step-desklet/conversation.json`
- Current provider selection: `~/.config/step-desklet/model-provider`
- Installed extension and private Python runtime: `~/.local/share/gnome-shell/extensions/zylo-command@local` and `~/.local/share/zylo-system-command-desklet`

## Development and troubleshooting

`install-extension.sh` copies the extension, bridge, and Python package into the user runtime, creates a virtual environment, installs `requirements.txt`, packages the GNOME extension, and enables it. Re-run it after source changes. Check extension state with:

```bash
gnome-extensions info zylo-command@local
```

The extension code is in `gnome_extension/`. `extension_bridge.py` starts one Python worker request at a time; the command and search behavior is in `step_desklet/core/worker.py`.

## Security

The assistant can run shell commands as the logged-in user. Review commands and results carefully, especially for requests involving deletion, system configuration, or elevated privileges. Search results are external input and should not be treated as instructions. Protect `~/.config/step-desklet.env`; never commit it or paste its contents into public issues.

## License

MIT. See [LICENSE](LICENSE).
