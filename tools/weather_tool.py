import requests
from langchain_core.tools import tool

from api.monitor import monitor

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


def _describe_weather(code: int) -> str:
    return WEATHER_CODES.get(int(code), f"未知天气({code})")


@tool
def get_weather(city: str, forecast_days: int = 3) -> str:
    """
    查询指定城市的实时天气和未来几天预报。
    适用于用户询问气温、下雨、出行天气等场景。
    :param city: 城市名称，例如 北京、上海、Hangzhou
    :param forecast_days: 预报天数，范围 1-7，默认 3
    :return: 当前天气和逐日预报文本
    """
    days = max(1, min(int(forecast_days), 7))
    monitor.report_tool(tool_name="天气查询工具", args={"city": city, "forecast_days": days})

    geo_resp = requests.get(
        GEOCODING_URL,
        params={"name": city, "count": 1, "language": "zh"},
        timeout=15,
    )
    geo_resp.raise_for_status()
    geo_results = geo_resp.json().get("results") or []
    if not geo_results:
        return f"未找到城市：{city}。请换一个更具体的城市名再试。"

    place = geo_results[0]
    latitude = place["latitude"]
    longitude = place["longitude"]
    location = " ".join(
        part for part in [place.get("name"), place.get("admin1"), place.get("country")] if part
    )

    forecast_resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
            "forecast_days": days,
        },
        timeout=15,
    )
    forecast_resp.raise_for_status()
    data = forecast_resp.json()

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    lines = [
        f"地点：{location}",
        f"当前：{_describe_weather(current.get('weather_code', -1))}，"
        f"气温 {current.get('temperature_2m', '未知')}°C，"
        f"湿度 {current.get('relative_humidity_2m', '未知')}%，"
        f"风速 {current.get('wind_speed_10m', '未知')} km/h",
        "预报：",
    ]
    dates = daily.get("time") or []
    for index, date in enumerate(dates):
        lines.append(
            f"- {date}：{_describe_weather((daily.get('weather_code') or [0])[index])}，"
            f"{(daily.get('temperature_2m_min') or ['?'])[index]}°C ~ "
            f"{(daily.get('temperature_2m_max') or ['?'])[index]}°C，"
            f"降水 {(daily.get('precipitation_sum') or ['?'])[index]} mm"
        )
    return "\n".join(lines)
