"""Match Windows application inventory entries without sending them to a model."""

import json
import ntpath
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from step_desklet.core.config import get_config_dir, read_json_file


SKILL_ID = "installed-apps"

_APP_OPEN = re.compile(
    r"^\s*(?:(?:please|(?:can|could|would|will) you)\s+)*"
    r"(?:open|launch|start)\s+([^\r\n]+?)\s*$",
    re.IGNORECASE,
)
_LIST_APPS = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"(?:list|show|see)(?:\s+me)?\s+(?:(?:all|the|my)\s+)*"
    r"(?:installed\s+(?:apps|applications|programs|software)|(?:apps|applications|programs|software)\s+installed)"
    r"(?:\s+(?:on|in)\s+(?:(?:this|my|the)\s+)?(?:pc|computer|windows))?"
    r"|(?:what|which)\s+(?:apps|applications|programs)\s+are\s+installed"
    r"(?:\s+on\s+(?:(?:this|my|the)\s+)?(?:pc|computer|windows))?"
    r"|(?:run|use)\s+(?:the\s+)?installed[ -]apps(?:\s+skill)?"
    r")[.!?]?\s*$",
    re.IGNORECASE,
)
_NON_APP = re.compile(
    r"^(?:(?:my|the|a|an)\s+)?(?:file|folder|document|website|web page|url|link|last|recent)\b"
    r"|\b(?:and|then|with|using|in|from|to|as)\s+"
    r"|\b(?:pdf|document|spreadsheet|file|folder)\s*$",
    re.IGNORECASE,
)
_GENERIC_TARGETS = {"command", "it", "that", "them", "this", "now", "process", "script", "service", "task"}
_APP_ID = re.compile(r"[\w.!{}\\-]{1,260}\Z")
_VERSION = re.compile(r"\s+v?\d+(?:\.\d+)+(?:\s.*)?$", re.IGNORECASE)
_MAINTENANCE = re.compile(
    r"\b(?:uninstall|updater|repair|reset (?:preferences|settings|cache)|release notes|readme|user guide)\b",
    re.IGNORECASE,
)
_SYSTEM_COMPONENT = re.compile(
    r"\b(?:redistributable|language pack|driver package|hotfix|desktop runtime|targeting pack|"
    r"app runtime|runtime broker|appx deployment|windows build|windows sdk|windows subsystem host)\b",
    re.IGNORECASE,
)
_ALIASES = {
    "chrome": "google chrome",
    "edge": "microsoft edge",
    "firefox": "mozilla firefox",
    "gemini": "google gemini",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "vlc": "vlc media player",
}
_PRIORITY = {"shortcut": 0, "apps-folder": 1, "executable": 2, "installed": 3}
CATALOG_VERSION = 3


@dataclass(frozen=True)
class InstalledApp:
    name: str
    kind: str
    target: str
    window_executable: str = ""

    @property
    def launch_target(self) -> str:
        if self.kind == "apps-folder":
            target = self.target.removeprefix("shell:AppsFolder\\")
            if _APP_ID.fullmatch(target) and ".." not in target and not target.startswith("\\"):
                return "shell:AppsFolder\\" + target
        elif self.kind in {"shortcut", "executable"}:
            drive, remainder = ntpath.splitdrive(self.target)
            suffixes = {".lnk", ".appref-ms"} if self.kind == "shortcut" else {".exe"}
            if (
                re.fullmatch(r"[a-zA-Z]:", drive)
                and remainder.startswith(("\\", "/"))
                and ":" not in remainder
                and "\x00" not in remainder
                and ntpath.splitext(remainder)[1].casefold() in suffixes
            ):
                return self.target
        return ""


@dataclass(frozen=True)
class AppCatalog:
    apps: list[InstalledApp]
    source: str
    updated: str


def load_app_catalog() -> AppCatalog | None:
    data = read_json_file(get_config_dir() / "installed-apps.json")
    if data.get("version") != CATALOG_VERSION or data.get("source") not in ("local", "groq"):
        return None
    try:
        apps = read_catalog(json.dumps(data["apps"]))
    except (KeyError, TypeError, ValueError):
        return None
    return AppCatalog(apps, data["source"], str(data.get("updated", "")))


def save_app_catalog(apps: list[InstalledApp], source: str) -> AppCatalog:
    catalog = AppCatalog(apps, source, date.today().isoformat())
    destination = get_config_dir() / "installed-apps.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         prefix="installed-apps-", suffix=".tmp",
                                         dir=destination.parent) as file:
            temp_path = Path(file.name)
            json.dump({
                "version": CATALOG_VERSION, "source": source, "updated": catalog.updated,
                "apps": [{"Name": app.name, "Kind": app.kind, "Target": app.target,
                          "WindowExecutable": app.window_executable} for app in apps],
            }, file, ensure_ascii=False)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return catalog


def curate_apps(
    apps: list[InstalledApp], classify: Callable[[list[str]], str] | None = None,
) -> list[InstalledApp]:
    groups: dict[str, list[InstalledApp]] = {}
    for app in apps:
        key = _identity(app.name)
        if key:
            groups.setdefault(key, []).append(app)

    candidates = [(catalog_names(group)[0], group) for group in groups.values()]
    candidates.sort(key=lambda item: item[0].casefold())
    if classify is None:
        return [app for name, group in candidates if not _SYSTEM_COMPONENT.search(name) for app in group]

    # Preserve desktop applications with a checked shortcut or executable even
    # when the classifier omits an unfamiliar product name.
    selected = []
    uncertain = []
    for name, group in candidates:
        if _SYSTEM_COMPONENT.search(name):
            continue
        if any(app.kind in {"shortcut", "executable"} and app.launch_target for app in group):
            selected.extend(group)
        else:
            uncertain.append((name, group))
    for start in range(0, len(uncertain), 160):
        batch = uncertain[start:start + 160]
        response = json.loads(classify([name for name, _ in batch]))
        ids = response.get("ids") if isinstance(response, dict) else None
        if not isinstance(ids, list) or any(type(index) is not int or not 1 <= index <= len(batch) for index in ids):
            raise ValueError("Groq returned an invalid app list.")
        for index in sorted(set(ids)):
            selected.extend(batch[index - 1][1])
    if apps and not selected:
        raise ValueError("Groq did not select any applications.")
    return selected


def requests_app_inventory(text: str) -> bool:
    return bool(_LIST_APPS.fullmatch(text or ""))


def open_app_target(text: str) -> str:
    """Recognize a single app-name launch; leave paths, URLs, and multi-step requests alone."""
    match = _APP_OPEN.fullmatch(text or "")
    if not match:
        return ""
    name = match[1].strip().rstrip(".!?").strip()
    name = re.sub(r"(?:\s+for me|\s*,?\s+please)$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"^(?:the|my)\s+", "", name, flags=re.IGNORECASE).strip("\"'")
    name = re.sub(r"\s+(?:app|application)$", "", name, flags=re.IGNORECASE)
    if (
        not name
        or len(name) > 90
        or name.casefold() in _GENERIC_TARGETS
        or _NON_APP.search(name)
        or not re.fullmatch(r"[\w .()+-]+", name)
        or re.search(r"\.[a-zA-Z]{2,5}$", name)
    ):
        return ""
    return name


def read_catalog(stdout: str) -> list[InstalledApp]:
    records = json.loads(stdout.lstrip("\ufeff"))
    if not isinstance(records, list):
        raise ValueError("Expected a Windows app catalog.")
    catalog = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("Name")
        kind = record.get("Kind")
        target = record.get("Target")
        window_executable = record.get("WindowExecutable")
        if (
            isinstance(name, str) and name.strip()
            and isinstance(kind, str) and kind in _PRIORITY
            and isinstance(target, str)
            and not _MAINTENANCE.search(name)
        ):
            catalog.append(InstalledApp(
                " ".join(name.split()), kind, target,
                window_executable if isinstance(window_executable, str) else "",
            ))
    return catalog


def _words(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(re.findall(r"[^\W_]+", name))


def _identity(name: str) -> str:
    name = _words(name)
    name = re.sub(r"\s+shortcut$", "", name)
    name = re.sub(r"\s+(?:desktop|app|application)$", "", name)
    return _ALIASES.get(name, name).replace(" ", "")


def app_matches(name: str, catalog: list[InstalledApp]) -> list[InstalledApp]:
    """Return best-ranked distinct titles; never guess between tied apps."""
    query = _words(name)
    query_key = _identity(name)
    if not query_key:
        return []
    alias = _ALIASES.get(query)
    ranked: dict[str, tuple[int, InstalledApp]] = {}
    for app in catalog:
        label = _words(app.name)
        identity = _identity(app.name)
        if query == label or label == alias:
            score = 0
        elif query_key == identity or (
            not _VERSION.search(name) and query_key == _identity(_VERSION.sub("", app.name))
        ):
            score = 1
        elif len(query_key) >= 4 and (
            label.startswith(query + " ") or label.endswith(" " + query)
        ):
            score = 2
        else:
            continue
        previous = ranked.get(identity)
        if previous is None or (
            not app.launch_target, score, _PRIORITY[app.kind]
        ) < (
            not previous[1].launch_target, previous[0], _PRIORITY[previous[1].kind]
        ):
            ranked[identity] = (score, app)
    if not ranked:
        return []
    best = min(score for score, _ in ranked.values())
    return sorted((app for score, app in ranked.values() if score == best), key=lambda app: app.name.casefold())


def catalog_names(catalog: list[InstalledApp]) -> list[str]:
    names: dict[str, InstalledApp] = {}
    for app in catalog:
        identity = _identity(app.name)
        previous = names.get(identity)
        if identity and (previous is None or len(app.name) > len(previous.name)):
            names[identity] = app
    return sorted((app.name for app in names.values()), key=str.casefold)


def app_launchers(app: InstalledApp, catalog: list[InstalledApp]) -> list[InstalledApp]:
    equivalents = [candidate for candidate in catalog if _identity(candidate.name) == _identity(app.name)]
    launchers = {}
    for candidate in sorted(equivalents, key=lambda candidate: _PRIORITY[candidate.kind]):
        if target := candidate.launch_target:
            launchers.setdefault(target.replace("/", "\\").casefold(), candidate)
    return list(launchers.values())


def app_web_fallback(name: str) -> str:
    if _identity(name) == _identity("Gemini"):
        return "https://gemini.google.com/app"
    return ""
