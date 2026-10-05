"""Weather from Open-Meteo (no key needed)."""

import requests

from . import tool

CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
         61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
         81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
         95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}

_geo_cache = {}


def geocode(name):
    key = name.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": name, "count": 1, "language": "en"}, timeout=8)
    res = (r.json().get("results") or [None])[0]
    if res:
        res = {"name": res["name"], "country": res.get("country", ""), "lat": res["latitude"],
               "lon": res["longitude"], "timezone": res.get("timezone", "auto")}
    _geo_cache[key] = res
    return res


@tool("Weather now and the forecast for the next days. Without a location, uses the home location.",
      {"location": ("string", "city name; empty for home"), "days": ("integer", "forecast days, 1-7")})
def get_weather(ctx, location="", days=3):
    place = location or ctx.settings["assistant"].get("location", "")
    if not place:
        return {"error": "no location given and no home location configured; ask the user for a city"}
    g = geocode(place)
    if not g:
        return {"error": "unknown place %r" % place}
    days = max(1, min(int(days or 3), 7))
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": g["lat"], "longitude": g["lon"], "timezone": g["timezone"], "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
    }, timeout=8)
    d = r.json()
    cur = d.get("current", {})
    daily = d.get("daily", {})
    forecast = []
    for i, day in enumerate(daily.get("time", [])):
        forecast.append({"date": day, "conditions": CODES.get(daily["weather_code"][i], "?"),
                         "max_c": daily["temperature_2m_max"][i], "min_c": daily["temperature_2m_min"][i],
                         "rain_chance_pct": daily["precipitation_probability_max"][i],
                         "rain_mm": daily["precipitation_sum"][i]})
    return {"place": "%s, %s" % (g["name"], g["country"]),
            "now": {"conditions": CODES.get(cur.get("weather_code"), "?"), "temp_c": cur.get("temperature_2m"),
                    "feels_like_c": cur.get("apparent_temperature"), "humidity_pct": cur.get("relative_humidity_2m"),
                    "wind_kmh": cur.get("wind_speed_10m")},
            "forecast": forecast}


EXAMPLES = {"en": ["What's the weather like?", "Will it rain tomorrow?", "What's the weather in Lyon this weekend?", "Do I need a coat today?"],
            "fr": ["Quel temps fait-il ?", "Est-ce qu'il va pleuvoir demain ?", "Quelle météo à Lyon ce week-end ?", "Il faut prendre un parapluie aujourd'hui ?"]}
