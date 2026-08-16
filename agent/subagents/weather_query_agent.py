from agent.prompts import sub_agents_content
from tools.weather_tool import get_weather

weather_query_agent = {
    "name": sub_agents_content["weather"]["name"],
    "description": sub_agents_content["weather"]["description"],
    "system_prompt": sub_agents_content["weather"]["system_prompt"],
    "tools": [get_weather],
}
