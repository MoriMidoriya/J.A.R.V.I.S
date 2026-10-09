"""Weather (Open-Meteo, no API key) and reading web pages (trafilatura text extraction)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import httpx
from loguru import logger
from pydantic import Field

from jarvis.config import get_settings
from jarvis.tools.registry import ToolError, tool

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
_geo_cache: dict[str, tuple[float, float, str]] = {}


def _geocode(city: str) -> tuple[float, float, str]:
    key = city.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    r = httpx.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1, "language": "en", "format": "json"}, timeout=8)
    res = (r.json() or {}).get("results") or []
    if not res:
        raise ToolError(f"I couldn't find a place called '{city}'.")
    g = res[0]
    label = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)
    _geo_cache[key] = (g["latitude"], g["longitude"], label)
    return _geo_cache[key]


def _locate_by_ip() -> tuple[float, float, str]:
    r = httpx.get("http://ip-api.com/json/?fields=status,city,regionName,country,lat,lon", timeout=6).json()
    if r.get("status") != "success":
        raise ToolError("I couldn't determine your location; tell me the city.")
    return r["lat"], r["lon"], ", ".join(x for x in (r.get("city"), r.get("regionName"), r.get("country")) if x)


@tool(summary="Weather: {city}")
def get_weather(city: Annotated[str | None, Field(description="City name. Leave null for the user's current location.")] = None) -> dict[str, Any]:
    """Current weather and today's forecast for a city (or here, if no city given)."""
    s = get_settings()
    try:
        if city:
            lat, lon, label = _geocode(city)
        elif s.assistant.home_city:
            lat, lon, label = _geocode(s.assistant.home_city)
        else:
            lat, lon, label = _locate_by_ip()
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon, "timezone": "auto",
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunset,sunrise",
                "forecast_days": 2,
            },
            timeout=8,
        ).json()
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Weather service unavailable: {e}") from e
    cur, day = r.get("current", {}), r.get("daily", {})
    out = {
        "location": label,
        "now": f"{round(cur.get('temperature_2m', 0))}°C, feels like {round(cur.get('apparent_temperature', 0))}°C, {WMO.get(cur.get('weather_code'), 'unknown')}, humidity {cur.get('relative_humidity_2m')}%, wind {round(cur.get('wind_speed_10m', 0))} km/h",
        "today": f"high {round(day['temperature_2m_max'][0])}°C, low {round(day['temperature_2m_min'][0])}°C, {WMO.get(day['weather_code'][0], '')}, rain chance {day['precipitation_probability_max'][0]}%",
        "tomorrow": f"high {round(day['temperature_2m_max'][1])}°C, low {round(day['temperature_2m_min'][1])}°C, {WMO.get(day['weather_code'][1], '')}, rain chance {day['precipitation_probability_max'][1]}%",
        "sunset_today": datetime.fromisoformat(day["sunset"][0]).strftime("%I:%M %p").lstrip("0"),
    }
    logger.debug("weather {}: {}", label, out["now"])
    return out


@tool(summary="Read {url}")
def fetch_page(
    url: Annotated[str, Field(description="Full URL of the page to read")],
    max_chars: Annotated[int, Field(ge=500, le=12000, description="How much text to return")] = 3500,
) -> str:
    """Download a web page and return its main text (for summarising or answering questions about it)."""
    import trafilatura

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/1.0"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Could not fetch {url}: {e}") from e
    text = trafilatura.extract(r.text, include_comments=False, include_tables=True, favor_recall=True) or ""
    if not text.strip():
        raise ToolError("The page had no readable article text (it may need JavaScript or a login).")
    return text if len(text) <= max_chars else text[:max_chars] + f"\n… [{len(text)} chars total]"
