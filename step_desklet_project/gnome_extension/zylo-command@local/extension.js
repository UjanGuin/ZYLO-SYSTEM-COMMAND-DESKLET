import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const RUNTIME_DIR = GLib.build_filenamev([GLib.get_user_data_dir(), 'zylo-system-command-desklet']);
const SETTINGS_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'step-desklet', 'settings.json']);
const MODEL_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'step-desklet', 'model-provider']);
const LAYOUT_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'step-desklet', 'layout.json']);

export default class ZyloCommandExtension extends Extension {
    enable() {
        this._settings = this._readSettings();
        this._provider = this._readProvider();
        this._layout = this._readLayout();
        this._locked = Boolean(this._layout.locked);
        this._buildDesklet();
        this._loadStylesheet();
        Main.layoutManager.addChrome(this._desklet, {trackFullscreen: false});
        this._applyAccent();
        this._desklet.set_position(this._layout.x ?? 72, this._layout.y ?? 96);
        if (this._layout.width && this._layout.height) {
            this._desklet.set_size(this._layout.width, this._layout.height);
            this._userHeight = this._layout.height;
        }
        this._focusChangedId = global.display.connect('notify::focus-window', () => this._updateDesktopVisibility());
        this._windowCreatedId = global.display.connect('window-created', (display, window) => {
            const type = window.get_window_type();
            if (type === Meta.WindowType.DESKTOP || type === Meta.WindowType.DOCK)
                return;
            this._hideDesklet();
        });
        this._overviewIsShowing = false;
        this._overviewShownId = Main.overview.connect('showing', () => {
            this._overviewIsShowing = true;
            this._hideDesklet();
        });
        this._overviewHiddenId = Main.overview.connect('hidden', () => {
            this._overviewIsShowing = false;
            this._updateDesktopVisibility();
        });
        this._updateDesktopVisibility();
    }

    disable() {
        this._saveLayout();
        this._cancelResponseAutoclear();
        this._cancelProviderStatusTimeout();
        this._subprocess?.force_exit();
        this._subprocess = null;
        if (this._stageCaptureId) {
            global.stage.disconnect(this._stageCaptureId);
            this._stageCaptureId = 0;
        }
        if (this._focusChangedId)
            global.display.disconnect(this._focusChangedId);
        if (this._windowCreatedId)
            global.display.disconnect(this._windowCreatedId);
        if (this._overviewShownId)
            Main.overview.disconnect(this._overviewShownId);
        if (this._overviewHiddenId)
            Main.overview.disconnect(this._overviewHiddenId);
        if (this._desklet) {
            Main.layoutManager.removeChrome(this._desklet);
            this._desklet.destroy();
            this._desklet = null;
        }
        this._unloadStylesheet();
    }

    _loadStylesheet() {
        this._stylesheet = this.dir.get_child('stylesheet.css');
        St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(this._stylesheet);
    }

    _unloadStylesheet() {
        if (this._stylesheet)
            St.ThemeContext.get_for_stage(global.stage).get_theme().unload_stylesheet(this._stylesheet);
    }

    _updateDesktopVisibility() {
        if (!this._desklet || this._interacting || this._overviewIsShowing)
            return;
        const focusedWindow = global.display.get_focus_window();
        if (focusedWindow &&
            focusedWindow.get_window_type() !== Meta.WindowType.DESKTOP &&
            focusedWindow.get_window_type() !== Meta.WindowType.DOCK) {
            this._hideDesklet();
        } else {
            this._desklet.show();
        }
    }

    _hideDesklet() {
        this._desklet?.hide();
    }

    _buildDesklet() {
        this._desklet = new St.BoxLayout({
            style_class: 'zylo-glass-card',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: true,
            width: 340,
        });

        const header = new St.BoxLayout({style_class: 'zylo-header'});
        const handle = new St.BoxLayout({style_class: 'zylo-handle', orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        handle.add_child(new St.Label({text: this._settings.name || 'ZYLO', style_class: 'zylo-title'}));
        handle.add_child(new St.Label({text: 'SYSTEM COMMAND', style_class: 'zylo-subtitle'}));
        this._addDragSupport(handle);
        header.add_child(handle);

        const actions = new St.BoxLayout({style_class: 'zylo-header-actions'});
        const clearButton = new St.Button({label: '↻', style_class: 'zylo-icon-button', can_focus: true});
        clearButton.connect('clicked', () => this._clear());
        actions.add_child(clearButton);
        this._modelButton = new St.Button({label: this._provider === 'groq' ? 'G' : 'N', style_class: 'zylo-icon-button zylo-model-button', can_focus: true});
        this._modelButton.connect('clicked', () => this._toggleProvider());
        actions.add_child(this._modelButton);
        this._lockButton = new St.Button({style_class: 'zylo-icon-button', can_focus: true});
        this._lockButton.connect('clicked', () => this._toggleLock());
        actions.add_child(this._lockButton);
        header.add_child(actions);
        this._desklet.add_child(header);

        this._response = new St.Label({style_class: 'zylo-response', visible: false, x_expand: true});
        this._response.clutter_text.line_wrap = true;
        this._response.clutter_text.line_wrap_mode = 2;
        this._response.clutter_text.ellipsize = 0;
        this._response.clutter_text.single_line_mode = false;
        this._desklet.add_child(this._response);
        this._status = new St.Label({style_class: 'zylo-status', visible: false, x_expand: true});
        this._status.clutter_text.line_wrap = true;
        this._desklet.add_child(this._status);

        const inputShell = new St.BoxLayout({style_class: 'zylo-input-shell'});
        this._inputShell = inputShell;
        this._entry = new St.Entry({style_class: 'zylo-input', hint_text: 'Ask ZYLO', can_focus: true, x_expand: true});
        this._entry.clutter_text.set_cursor_visible(true);
        this._entry.connect('button-press-event', () => {
            this._entry.grab_key_focus();
            this._entry.clutter_text.set_cursor_visible(true);
            return Clutter.EVENT_PROPAGATE;
        });
        this._entry.clutter_text.connect('activate', () => this._send());
        inputShell.add_child(this._entry);
        this._sendButton = new St.Button({label: '→', style_class: 'zylo-send', can_focus: true});
        this._sendButton.connect('clicked', () => this._send());
        inputShell.add_child(this._sendButton);
        this._desklet.add_child(inputShell);
        const footer = new St.BoxLayout({style_class: 'zylo-footer', x_expand: true});
        footer.add_child(new St.Widget({x_expand: true}));
        this._resizeHandle = new St.Button({label: '⤢', style_class: 'zylo-resize-handle', can_focus: false});
        this._resizeHandle.connect('button-press-event', (actor, event) => this._beginPointerResize(event));
        footer.add_child(this._resizeHandle);
        this._desklet.add_child(footer);
        this._footer = footer;
        this._updateLockButton();
    }

    _readSettings() {
        try {
            const [ok, contents] = GLib.file_get_contents(SETTINGS_FILE);
            if (ok)
                return JSON.parse(new TextDecoder().decode(contents));
        } catch (error) {
            log(`ZYLO settings could not be read: ${error.message}`);
        }
        return {};
    }

    _applyAccent() {
        const accent = /^#[0-9a-fA-F]{6}$/.test(this._settings.accent || '') ? this._settings.accent : '#a78bfa';
        const rgb = accent.match(/[0-9a-fA-F]{2}/g).map(value => parseInt(value, 16));
        this._desklet.set_style(`border-color: ${accent};`);
        this._inputShell.set_style(`border-color: rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.58);`);
        this._modelButton.set_style(`color: ${accent};`);
        this._lockButton.set_style(`color: ${accent};`);
        // Keep the established lavender send button; the selected accent is
        // used for the card, input outline, and header controls.
    }

    _readProvider() {
        try {
            const [ok, contents] = GLib.file_get_contents(MODEL_FILE);
            if (ok && new TextDecoder().decode(contents).trim() === 'groq')
                return 'groq';
        } catch (_) {
            // NVIDIA NIM is the default until the user selects Groq.
        }
        return 'nim';
    }

    _readLayout() {
        try {
            const [ok, contents] = GLib.file_get_contents(LAYOUT_FILE);
            if (ok)
                return JSON.parse(new TextDecoder().decode(contents));
        } catch (error) {
            log(`ZYLO layout could not be read: ${error.message}`);
        }
        return {};
    }

    _saveLayout() {
        if (!this._desklet)
            return;
        const layout = {
            x: Math.round(this._desklet.x),
            y: Math.round(this._desklet.y),
            width: Math.round(this._desklet.width),
            height: Math.round(this._desklet.height),
            locked: this._locked,
        };
        try {
            GLib.mkdir_with_parents(GLib.path_get_dirname(LAYOUT_FILE), 0o700);
            GLib.file_set_contents(LAYOUT_FILE, JSON.stringify(layout));
        } catch (error) {
            log(`ZYLO layout could not be saved: ${error.message}`);
        }
    }

    _toggleProvider() {
        this._cancelProviderStatusTimeout();
        this._provider = this._provider === 'groq' ? 'nim' : 'groq';
        this._modelButton.set_label(this._provider === 'groq' ? 'G' : 'N');
        try {
            GLib.mkdir_with_parents(GLib.path_get_dirname(MODEL_FILE), 0o700);
            GLib.file_set_contents(MODEL_FILE, this._provider);
            this._status.text = this._provider === 'groq' ? 'Groq · GPT OSS 120B selected' : 'NVIDIA · Step 5 selected';
            this._status.show();
            this._fitDeskletToContent();
            this._providerStatusTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2200, () => {
                this._providerStatusTimeoutId = 0;
                this._status.hide();
                this._fitDeskletToContent();
                return GLib.SOURCE_REMOVE;
            });
        } catch (error) {
            log(`ZYLO model selection could not be saved: ${error.message}`);
        }
    }

    _addDragSupport(handle) {
        handle.reactive = true;
        handle.connect('button-press-event', (actor, event) => {
            if (this._locked || event.get_button() !== Clutter.BUTTON_PRIMARY)
                return Clutter.EVENT_PROPAGATE;
            const [x, y] = event.get_coords();
            this._windowStartX = this._desklet.x;
            this._windowStartY = this._desklet.y;
            this._beginPointerTracking('move', x, y);
            return Clutter.EVENT_STOP;
        });
    }

    _beginPointerResize(event) {
        if (this._locked)
            return Clutter.EVENT_PROPAGATE;
        if (event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        const [x, y] = event.get_coords();
        this._sizeStartWidth = this._desklet.width;
        this._sizeStartHeight = this._desklet.height;
        this._beginPointerTracking('resize', x, y);
        return Clutter.EVENT_STOP;
    }

    _beginPointerTracking(mode, startX, startY) {
        this._pointerStartX = startX;
        this._pointerStartY = startY;
        this._interactionMode = mode;
        this._interacting = true;
        this._stageCaptureId = global.stage.connect('captured-event', (stage, event) => {
            const type = event.type();
            if (type === Clutter.EventType.MOTION) {
                const [x, y] = event.get_coords();
                const dx = x - this._pointerStartX;
                const dy = y - this._pointerStartY;
                if (this._interactionMode === 'move') {
                    this._desklet.set_position(this._windowStartX + dx, this._windowStartY + dy);
                } else {
                    const maxWidth = Math.max(280, global.stage.width - this._desklet.x - 16);
                    const maxHeight = Math.max(92, global.stage.height - this._desklet.y - 64);
                    const width = Math.min(maxWidth, Math.max(280, this._sizeStartWidth + dx));
                    const height = Math.min(maxHeight, Math.max(92, this._sizeStartHeight + dy));
                    this._desklet.set_size(width, height);
                }
                return Clutter.EVENT_STOP;
            }
            if (type === Clutter.EventType.BUTTON_RELEASE) {
                global.stage.disconnect(this._stageCaptureId);
                this._stageCaptureId = 0;
                if (this._interactionMode === 'resize')
                    this._userHeight = this._desklet.height;
                this._saveLayout();
                this._interactionMode = null;
                this._interacting = false;
                this._updateDesktopVisibility();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
    }

    _toggleLock() {
        this._locked = !this._locked;
        this._updateLockButton();
        this._fitDeskletToContent();
        this._saveLayout();
    }

    _updateLockButton() {
        this._lockButton.set_label(this._locked ? '🔒' : '🔓');
        this._footer.visible = !this._locked;
    }

    _clear() {
        this._cancelResponseAutoclear();
        this._response.text = '';
        this._response.hide();
        this._status.hide();
        this._fitDeskletToContent();
        this._entry.set_text('');
        this._entry.grab_key_focus();
    }

    _send() {
        const prompt = this._entry.get_text().trim();
        if (!prompt || this._subprocess)
            return;
        this._cancelProviderStatusTimeout();
        this._cancelResponseAutoclear();
        this._entry.set_text('');
        this._entry.reactive = false;
        this._sendButton.reactive = false;
        this._response.hide();
        this._status.text = 'Thinking…';
        this._status.show();
        this._fitDeskletToContent();

        const python = GLib.build_filenamev([RUNTIME_DIR, 'venv', 'bin', 'python']);
        const bridge = GLib.build_filenamev([RUNTIME_DIR, 'extension_bridge.py']);
        if (!GLib.file_test(python, GLib.FileTest.IS_EXECUTABLE) || !GLib.file_test(bridge, GLib.FileTest.EXISTS)) {
            this._finishRequest({ok: false, error: 'ZYLO runtime is not installed. Run install-extension.sh from the project folder.'});
            return;
        }
        try {
            this._subprocess = Gio.Subprocess.new([python, bridge], Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            const input = this._subprocess.get_stdin_pipe();
            input.write_all(new TextEncoder().encode(JSON.stringify({prompt, provider: this._provider})), null);
            input.close(null);
            this._bridgeOutput = new Gio.DataInputStream({base_stream: this._subprocess.get_stdout_pipe()});
            this._readBridgeOutput();
        } catch (error) {
            this._finishRequest({ok: false, error: error.message});
        }
    }

    _readBridgeOutput() {
        this._bridgeOutput.read_line_async(GLib.PRIORITY_DEFAULT, null, (stream, result) => {
            let line;
            try {
                [line] = stream.read_line_finish_utf8(result);
            } catch (error) {
                this._finishRequest({ok: false, error: error.message});
                return;
            }
            if (line === null) {
                this._finishRequest({ok: false, error: 'The command bridge ended without a result.'});
                return;
            }
            try {
                const event = JSON.parse(line);
                if (event.type === 'status') {
                    this._status.text = event.status;
                    this._fitDeskletToContent();
                } else if (event.type === 'result') {
                    this._finishRequest(event);
                    return;
                }
            } catch (error) {
                log(`ZYLO bridge output could not be read: ${error.message}`);
            }
            this._readBridgeOutput();
        });
    }

    _finishRequest(payload) {
        this._subprocess = null;
        this._entry.reactive = true;
        this._sendButton.reactive = true;
        this._status.hide();
        this._response.text = payload.ok ? payload.response : `Error: ${payload.error || 'Request failed.'}`;
        this._response.show();
        this._fitDeskletToContent();
        if (payload.ok)
            this._scheduleResponseAutoclear(payload.response || '');
        this._entry.grab_key_focus();
        this._entry.clutter_text.set_cursor_visible(true);
    }

    _scheduleResponseAutoclear(response) {
        this._cancelResponseAutoclear();
        const wordCount = response.trim().split(/\s+/).filter(Boolean).length;
        const delay = Math.max(6000, Math.min(120000, 3500 + wordCount * 220));
        this._responseTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._response.text = '';
            this._response.hide();
            this._responseTimeoutId = 0;
            this._fitDeskletToContent();
            return GLib.SOURCE_REMOVE;
        });
    }

    _fitDeskletToContent() {
        if (!this._desklet)
            return;
        this._desklet.queue_relayout();
        const children = this._desklet.get_children().filter(child => child.visible);
        const contentWidth = Math.max(1, this._desklet.width - 28);
        let naturalHeight = 15 + Math.max(0, children.length - 1) * 2;
        for (const child of children) {
            const [, childHeight] = child.get_preferred_height(contentWidth);
            naturalHeight += childHeight;
        }
        const height = Math.max(Math.ceil(naturalHeight), this._userHeight || 0);
        if (height > 0)
            this._desklet.set_height(height);
    }

    _cancelResponseAutoclear() {
        if (!this._responseTimeoutId)
            return;
        GLib.Source.remove(this._responseTimeoutId);
        this._responseTimeoutId = 0;
    }

    _cancelProviderStatusTimeout() {
        if (!this._providerStatusTimeoutId)
            return;
        GLib.Source.remove(this._providerStatusTimeoutId);
        this._providerStatusTimeoutId = 0;
    }
}
