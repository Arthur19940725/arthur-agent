from agent.llm import pro_model
from agent.prompts import sub_agents_content
from tools.stock_tool import get_stock_quote

stock_analysis_agent = {
    "name": sub_agents_content["stock"]["name"],
    "description": sub_agents_content["stock"]["description"],
    "system_prompt": sub_agents_content["stock"]["system_prompt"],
    "tools": [get_stock_quote],
    "model": pro_model,
}
