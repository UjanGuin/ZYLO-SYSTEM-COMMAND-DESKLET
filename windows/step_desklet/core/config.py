"""Cross-platform configuration, credential, and Windows startup helpers for ZYLO."""

import json
import math
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

IS_WINDOWS = sys.platform == "win32"

DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
FREELLMAPI_BASE_URL = "http://127.0.0.1:31415/v1"
FREELLMAPI_ROUTES = {"auto": "Auto", "auto:fast": "Fast"}
PROVIDER_LABELS = {
    "nim": "NVIDIA NIM",
    "groq": "Groq",
    "lmstudio": "LM Studio",
    "freellmapi": "FreeLLMAPI",
}
DEFAULT_MEMORY_TURNS = 12
MIN_MEMORY_TURNS = 4
MAX_MEMORY_TURNS = 30
DEFAULT_FILE_SEARCH_LEVELS = {
    "desktop": 7,
    "documents": 7,
    "downloads": 7,
    "pictures": 6,
    "music": 6,
    "videos": 6,
}
MAX_FILE_SEARCH_LEVEL_INPUT = 2_147_483_647

DEFAULT_DESKLET_X = 72
DEFAULT_DESKLET_Y = 96
DEFAULT_DESKLET_WIDTH = 364
MIN_DESKLET_WIDTH = 290
MAX_DESKLET_WIDTH = 720
MIN_DESKLET_HEIGHT = 96
MAX_DESKLET_HEIGHT = 900
MIN_DESKLET_COORD = -16384
MAX_DESKLET_COORD = 16384

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

ACCENTS: Dict[str, str] = {
    "Dark Violet": "#a78bfa",
    "Violet": "#b39aff",
    "Mint": "#75e0bd",
    "Rose": "#ff9eb5",
    "Amber": "#ffd27a",
}

GRADIENT_PRESETS: Dict[str, tuple[str, str, str]] = {
    "Aurora Pulse": ("#a78bfa", "#38bdf8", "#f472b6"),
    "Sunset Ember": ("#fb923c", "#f43f5e", "#c084fc"),
    "Oceanic": ("#60a5fa", "#2dd4bf", "#a78bfa"),
    "Cyber Mint": ("#34d399", "#22d3ee", "#818cf8"),
    "Rose Gold": ("#f472b6", "#fbbf24", "#c084fc"),
}


def _valid_hex(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if _HEX_COLOR_RE.match(candidate) else fallback


def get_accent_config(settings: Dict[str, Any] | None = None) -> Dict[str, str]:
    data = settings if isinstance(settings, dict) else {}
    mode = "gradient" if data.get("accentMode") == "gradient" else "solid"
    primary = _valid_hex(data.get("accent"), ACCENTS["Dark Violet"])
    start = _valid_hex(data.get("gradientStart"), primary)
    end = _valid_hex(data.get("gradientEnd"), "#38bdf8")
    mid = _valid_hex(data.get("gradientMid"), "#f472b6")
    return {
        "mode": mode,
        "accent": primary,
        "gradientStart": start,
        "gradientMid": mid,
        "gradientEnd": end,
    }

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "ZYLODesktopAssistant"


def get_config_dir() -> Path:
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "ZYLO"
    return Path.home() / ".config" / "step-desklet"


def get_env_file() -> Path:
    if IS_WINDOWS:
        return get_config_dir() / "step-desklet.env"
    return Path.home() / ".config" / "step-desklet.env"


def get_settings_file() -> Path:
    return get_config_dir() / "settings.json"


def get_layout_file() -> Path:
    return get_config_dir() / "layout.json"


def get_model_file() -> Path:
    return get_config_dir() / "model-provider"


def get_conversation_file() -> Path:
    return get_config_dir() / "conversation.json"


def get_skills_policy_file() -> Path:
    return get_config_dir() / "skills-policy.json"


def get_skill_history_file() -> Path:
    return get_config_dir() / "skill-history.json"


def ensure_config_dir() -> Path:
    cfg = get_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        try:
            cfg.chmod(0o700)
        except OSError:
            pass
    return cfg


def read_json_file(path: Path, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else (default or {})
    except (OSError, ValueError):
        return dict(default or {})


def write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    ensure_config_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not IS_WINDOWS:
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def refresh_local_date() -> None:
    """Save the Windows calendar date when it changes."""
    path = get_config_dir() / "local-date.json"
    today = date.today().isoformat()
    if read_json_file(path).get("date") == today:
        return
    try:
        write_json_file(path, {"date": today})
    except OSError:
        # The model can still get the date directly from the system clock.
        pass


def parse_bounded_int(
    value: Any,
    default: Optional[int],
    min_val: int,
    max_val: int,
) -> Optional[int]:
    """Safely parse an int within [min_val, max_val]; reject bools, NaN/inf, and invalid types."""
    if value is None or isinstance(value, bool):
        return default
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                return default
            num = int(round(value))
        elif isinstance(value, int):
            num = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            parsed_float = float(stripped)
            if not math.isfinite(parsed_float):
                return default
            num = int(round(parsed_float))
        else:
            return default
    except (TypeError, ValueError, OverflowError):
        return default
    return max(min_val, min(max_val, num))


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def validate_layout_config(raw: Any) -> Dict[str, Any]:
    """Normalize layout.json values into guaranteed bounded numeric desklet geometry."""
    data = raw if isinstance(raw, dict) else {}
    x = parse_bounded_int(data.get("x"), DEFAULT_DESKLET_X, MIN_DESKLET_COORD, MAX_DESKLET_COORD)
    y = parse_bounded_int(data.get("y"), DEFAULT_DESKLET_Y, MIN_DESKLET_COORD, MAX_DESKLET_COORD)
    width = parse_bounded_int(
        data.get("width"), DEFAULT_DESKLET_WIDTH, MIN_DESKLET_WIDTH, MAX_DESKLET_WIDTH
    )
    height = parse_bounded_int(
        data.get("height"), None, MIN_DESKLET_HEIGHT, MAX_DESKLET_HEIGHT
    )
    locked = parse_bool(data.get("locked"), False)
    return {
        "x": DEFAULT_DESKLET_X if x is None else x,
        "y": DEFAULT_DESKLET_Y if y is None else y,
        "width": DEFAULT_DESKLET_WIDTH if width is None else width,
        "height": height,
        "locked": locked,
    }


def load_layout_config() -> Dict[str, Any]:
    return validate_layout_config(read_json_file(get_layout_file()))


def validate_file_search_levels(raw: Any) -> Dict[str, int]:
    """Use 0 for unlimited. Invalid or missing settings keep the folder default."""
    data = raw if isinstance(raw, dict) else {}
    levels = DEFAULT_FILE_SEARCH_LEVELS.copy()
    for folder in levels:
        value = data.get(folder)
        if type(value) is int and 0 <= value <= MAX_FILE_SEARCH_LEVEL_INPUT:
            levels[folder] = value
    return levels


def validate_settings_config(raw: Any) -> Dict[str, Any]:
    """Normalize settings.json fields with safe defaults and bounds."""
    data = raw if isinstance(raw, dict) else {}
    accent_cfg = get_accent_config(data)
    provider = str(data.get("defaultProvider") or "").strip().lower()
    if provider not in enabled_providers(data):
        provider = ""
    raw_name = data.get("name")
    name = str(raw_name).strip()[:80] if isinstance(raw_name, str) else ""
    raw_lms_url = data.get("lmstudioBaseUrl")
    lms_url = (
        str(raw_lms_url).strip()[:250]
        if isinstance(raw_lms_url, str) and raw_lms_url.strip()
        else DEFAULT_LMSTUDIO_BASE_URL
    )
    raw_lms_model = data.get("lmstudioModel")
    lms_model = str(raw_lms_model).strip()[:160] if isinstance(raw_lms_model, str) else ""
    turns = parse_bounded_int(
        data.get("memoryTurns"),
        DEFAULT_MEMORY_TURNS,
        MIN_MEMORY_TURNS,
        MAX_MEMORY_TURNS,
    )
    return {
        "name": name,
        "accent": accent_cfg["accent"],
        "accentMode": accent_cfg["mode"],
        "gradientStart": accent_cfg["gradientStart"],
        "gradientMid": accent_cfg["gradientMid"],
        "gradientEnd": accent_cfg["gradientEnd"],
        "defaultProvider": provider,
        "lmstudioBaseUrl": lms_url,
        "lmstudioModel": lms_model,
        "lmstudioEnabled": parse_bool(data.get("lmstudioEnabled"), True),
        "freeLlmApiEnabled": parse_bool(data.get("freeLlmApiEnabled"), False),
        "freeLlmApiModel": normalize_freellmapi_route(data.get("freeLlmApiModel")),
        "memoryTurns": DEFAULT_MEMORY_TURNS if turns is None else turns,
        "launchOnStartup": parse_bool(data.get("launchOnStartup"), False),
        "allowAdminToolRequests": data.get("allowAdminToolRequests") is True,
        "filterAppsWithGroq": parse_bool(data.get("filterAppsWithGroq"), True),
        "fileSearchLevels": validate_file_search_levels(data.get("fileSearchLevels")),
    }


def load_settings_config() -> Dict[str, Any]:
    return validate_settings_config(read_json_file(get_settings_file()))


def normalize_freellmapi_route(value: Any) -> str:
    return value if isinstance(value, str) and value in FREELLMAPI_ROUTES else "auto"


def enabled_providers(settings: Dict[str, Any]) -> list[str]:
    providers = ["nim", "groq"]
    if not IS_WINDOWS or parse_bool(settings.get("lmstudioEnabled"), True):
        providers.append("lmstudio")
    if IS_WINDOWS and parse_bool(settings.get("freeLlmApiEnabled"), False):
        providers.append("freellmapi")
    return providers


def read_provider(settings: Dict[str, Any] | None = None) -> str:
    settings = load_settings_config() if settings is None else settings
    allowed = enabled_providers(settings)
    try:
        value = get_model_file().read_text(encoding="utf-8").strip().lower()
        if value in allowed:
            return value
    except OSError:
        pass
    default_provider = settings.get("defaultProvider")
    if default_provider in allowed:
        return default_provider
    return "nim"


def write_provider(provider: str) -> None:
    ensure_config_dir()
    normalized = provider if provider in enabled_providers(load_settings_config()) else "nim"
    get_model_file().write_text(normalized, encoding="utf-8")


def get_memory_turns(settings: Dict[str, Any] | None = None) -> int:
    data = settings if settings is not None else read_json_file(get_settings_file())
    raw = data.get("memoryTurns") or os.getenv("ZYLO_MEMORY_TURNS") or DEFAULT_MEMORY_TURNS
    try:
        turns = int(raw)
    except (TypeError, ValueError):
        turns = DEFAULT_MEMORY_TURNS
    return max(MIN_MEMORY_TURNS, min(MAX_MEMORY_TURNS, turns))


def read_env_keys() -> Dict[str, str]:
    keys = {
        "NVIDIA_API_KEY": "",
        "NVIDIA_BACKUP_API_KEY": "",
        "GROQ_API_KEY": "",
        "GROQ_BACKUP_API_KEY": "",
        "TAVILY_API_KEY": "",
        "LMSTUDIO_BASE_URL": "",
        "LMSTUDIO_MODEL": "",
        "LMSTUDIO_API_KEY": "",
        "FREELLMAPI_API_KEY": "",
    }
    env_file = get_env_file()
    if not env_file.exists():
        return keys
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k in ("NVIDIA_API_KEY", "NIM_API_KEY") and v:
                keys["NVIDIA_API_KEY"] = v
            elif k in keys and v:
                keys[k] = v
    except OSError:
        pass
    return keys


def write_env_keys(
    nvidia_key: str = "",
    groq_key: str = "",
    tavily_key: str = "",
    lmstudio_base_url: str = "",
    lmstudio_model: str = "",
    lmstudio_api_key: str = "",
    *,
    nvidia_backup_key: str | None = None,
    groq_backup_key: str | None = None,
    freellmapi_api_key: str | None = None,
) -> None:
    ensure_config_dir()
    env_file = get_env_file()
    existing_lines = []
    if env_file.exists():
        try:
            existing_lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing_lines = []

    updates = {
        "NVIDIA_API_KEY": nvidia_key.strip(),
        "GROQ_API_KEY": groq_key.strip(),
        "TAVILY_API_KEY": tavily_key.strip(),
        "LMSTUDIO_BASE_URL": lmstudio_base_url.strip(),
        "LMSTUDIO_MODEL": lmstudio_model.strip(),
        "LMSTUDIO_API_KEY": lmstudio_api_key.strip(),
    }
    if nvidia_backup_key is not None:
        updates["NVIDIA_BACKUP_API_KEY"] = nvidia_backup_key.strip()
    if groq_backup_key is not None:
        updates["GROQ_BACKUP_API_KEY"] = groq_backup_key.strip()
    if freellmapi_api_key is not None:
        updates["FREELLMAPI_API_KEY"] = freellmapi_api_key.strip()

    out_lines = []
    seen = set()
    for line in existing_lines:
        key_text, separator, _ = line.partition("=")
        matched_key = key_text.strip()
        if separator and matched_key in updates:
            seen.add(matched_key)
            val = updates[matched_key]
            if val:
                out_lines.append(f"{matched_key}={val}")
        else:
            out_lines.append(line)

    for key, val in updates.items():
        if val and key not in seen:
            out_lines.append(f"{key}={val}")

    env_file.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    if not IS_WINDOWS:
        try:
            env_file.chmod(0o600)
        except OSError:
            pass


def is_windows_startup_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, STARTUP_REG_NAME)
            return True
    except OSError:
        return False


def set_windows_startup(enabled: bool, command: str | None = None) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        ) as key:
            if enabled:
                if not command:
                    if getattr(sys, "frozen", False):
                        command = f'"{sys.executable}" --background'
                    else:
                        entry = Path(__file__).resolve().parents[2] / "zylo_win11.py"
                        pythonw = Path(sys.executable).with_name("pythonw.exe")
                        exe = str(pythonw if pythonw.exists() else sys.executable)
                        command = f'"{exe}" "{entry}" --background'
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
