"""Weather for the widget, from Open-Meteo (open-meteo.com): free, no API
key, so it works the same for everyone who installs Tavinor.

    place = find("Noida")       # {"name", "region", "country", "lat", "lon"}
    now = get(place, "c")       # current, today, air quality; cached 15 min

Only the coordinates of the place you chose are sent; nothing about you.
"""
import json
import threading
import time
import urllib.parse
import urllib.request

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
CACHE_FOR = 15 * 60
_cache = {}
_lock = threading.Lock()

# WMO weather codes (Open-Meteo's docs) -> words and an emoji the widget shows.
CODES = {
    0: ("Clear", "☀️", "🌙"), 1: ("Mostly clear", "🌤️", "🌙"), 2: ("Partly cloudy", "⛅", "☁️"),
    3: ("Cloudy", "☁️", "☁️"), 45: ("Fog", "🌫️", "🌫️"), 48: ("Freezing fog", "🌫️", "🌫️"),
    51: ("Light drizzle", "🌦️", "🌧️"), 53: ("Drizzle", "🌦️", "🌧️"), 55: ("Heavy drizzle", "🌧️", "🌧️"),
    56: ("Freezing drizzle", "🌧️", "🌧️"), 57: ("Freezing drizzle", "🌧️", "🌧️"),
    61: ("Light rain", "🌦️", "🌧️"), 63: ("Rain", "🌧️", "🌧️"), 65: ("Heavy rain", "🌧️", "🌧️"),
    66: ("Freezing rain", "🌧️", "🌧️"), 67: ("Freezing rain", "🌧️", "🌧️"),
    71: ("Light snow", "🌨️", "🌨️"), 73: ("Snow", "🌨️", "🌨️"), 75: ("Heavy snow", "❄️", "❄️"),
    77: ("Snow grains", "🌨️", "🌨️"), 80: ("Showers", "🌦️", "🌧️"), 81: ("Showers", "🌧️", "🌧️"),
    82: ("Heavy showers", "⛈️", "⛈️"), 85: ("Snow showers", "🌨️", "🌨️"), 86: ("Snow showers", "❄️", "❄️"),
    95: ("Thunderstorm", "⛈️", "⛈️"), 96: ("Thunderstorm, hail", "⛈️", "⛈️"), 99: ("Thunderstorm, hail", "⛈️", "⛈️"),
}

# US AQI bands (EPA).
AQI = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
       (200, "Unhealthy"), (300, "Very unhealthy"), (10 ** 9, "Hazardous")]


def _get(url, params, timeout=12):
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=timeout) as r:
        return json.load(r)


def find(name):
    """The best match for a place name, or None."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        hits = _get(GEO, {"name": name, "count": 1, "language": "en", "format": "json"}).get("results") or []
    except (OSError, ValueError):
        raise RuntimeError("couldn't reach the weather service") from None
    if not hits:
        return None
    h = hits[0]
    return {"name": h["name"], "region": h.get("admin1", ""), "country": h.get("country", ""),
            "lat": round(h["latitude"], 3), "lon": round(h["longitude"], 3)}


def describe(code, day=True):
    words, sun, moon = CODES.get(int(code), ("—", "🌡️", "🌡️"))
    return words, sun if day else moon


def aqi_band(v):
    return next(label for top, label in AQI if v <= top) if v is not None else None


def get(place, units="c"):
    """Current conditions, today's range, rain chance, sun times, UV and air
    quality for a place from find(). Cached 15 minutes per place and units."""
    key = (place["lat"], place["lon"], units)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_FOR:
            return hit[1]
    f = _get(FORECAST, {
        "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 2,
        "temperature_unit": "fahrenheit" if units == "f" else "celsius",
        "wind_speed_unit": "mph" if units == "f" else "kmh",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,"
                 "uv_index_max,weather_code"})
    try:
        air = _get(AIR, {"latitude": place["lat"], "longitude": place["lon"], "current": "us_aqi"})
        aqi = air.get("current", {}).get("us_aqi")
    except (OSError, ValueError):
        aqi = None                               # the forecast still counts without it
    c, d = f["current"], f["daily"]
    words, icon = describe(c["weather_code"], bool(c.get("is_day", 1)))
    out = {
        "place": place["name"], "units": "°F" if units == "f" else "°C",
        "temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
        "humidity": c["relative_humidity_2m"], "wind": round(c["wind_speed_10m"]),
        "wind_unit": "mph" if units == "f" else "km/h",
        "summary": words, "icon": icon,
        "high": round(d["temperature_2m_max"][0]), "low": round(d["temperature_2m_min"][0]),
        "rain": d["precipitation_probability_max"][0],
        "sunrise": d["sunrise"][0][-5:], "sunset": d["sunset"][0][-5:],
        "uv": round(d["uv_index_max"][0] or 0),
        "aqi": aqi, "aqi_label": aqi_band(aqi),
        "tomorrow": {"summary": describe(d["weather_code"][1])[0],
                     "high": round(d["temperature_2m_max"][1]), "low": round(d["temperature_2m_min"][1])},
    }
    with _lock:
        _cache[key] = (time.time(), out)
    return out
