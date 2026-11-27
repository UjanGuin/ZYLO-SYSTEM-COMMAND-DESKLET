"""User preferences and fresh desktop context for assistant replies."""

import math
import re
import time
from datetime import datetime

import requests


WRITING_TASKS = {
    "proofread": ("Proofread", "Correct spelling, grammar, and punctuation. Preserve meaning and tone."),
    "rewrite": ("Rewrite", "Improve clarity and flow while preserving the original meaning and language."),
    "summarize": ("Summarize", "Summarize the key points in the language of the original."),
    "expand": ("Expand", "Expand the text with useful detail. Do not invent new facts."),
}


def writing_task_for_source(source):
    task = source.removeprefix("writing:") if isinstance(source, str) and source.startswith("writing:") else ""
    return task if task in WRITING_TASKS else None


def weather_coordinates(value):
    try:
        latitude, longitude = map(float, value.split(","))
    except (AttributeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None


def _weather(settings, query):
    from step_desklet.core.config import get_config_dir, read_json_file, write_json_file

    coordinates = weather_coordinates(settings.get("weatherLocation"))
    if not coordinates or not re.search(r"\b(weather|temperature|rain|forecast|humidity|wind)\b", query, re.I):
        return ""
    path = get_config_dir() / "local-weather.json"
    cache = read_json_file(path)
    if cache.get("coordinates") == list(coordinates) and isinstance(cache.get("fetched"), (int, float)):
        if 0 <= time.time() - cache["fetched"] < 900 and isinstance(cache.get("summary"), str):
            return cache["summary"]
    try:
        with requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": coordinates[0], "longitude": coordinates[1],
                    "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
                    "timezone": "auto"},
            timeout=(3, 5), allow_redirects=False,
        ) as response:
            response.raise_for_status()
            current = response.json()["current"]
        temperature, humidity, rain, wind = (
            float(current[key]) for key in
            ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m")
        )
        if not all(math.isfinite(value) for value in (temperature, humidity, rain, wind)):
            return ""
        summary = (f"Open-Meteo current conditions at the user's configured local location: "
                   f"{temperature:g} °C, humidity {humidity:g}%, precipitation {rain:g} mm, "
                   f"wind {wind:g} km/h. Use these current conditions only for the configured local location, "
                   "and look elsewhere for a forecast if available.")
    except (requests.RequestException, KeyError, TypeError, ValueError):
        return ""
    try:
        write_json_file(path, {
            "coordinates": list(coordinates), "fetched": time.time(), "summary": summary,
        })
    except OSError:
        pass
    return summary


def assistant_context(settings, query, *, writing=False):
    parts = []
    instructions = settings.get("customInstructions", "")
    if instructions:
        parts.append(
            "User preferences for replies. Apply them where relevant and keep the app's response "
            "protocol and action permissions intact:\n" + instructions
        )
    if not writing:
        local = datetime.now().astimezone()
        offset = local.strftime("%z")
        parts.append(
            f"Current local date and time from this PC: {local:%Y-%m-%d %H:%M} "
            f"(UTC{offset[:3]}:{offset[3:]}). Mention it only when relevant."
        )
        if weather := _weather(settings, query):
            parts.append(weather)
    return "\n\n".join(parts)
