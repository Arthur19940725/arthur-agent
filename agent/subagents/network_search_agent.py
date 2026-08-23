from agent.prompts import sub_agents_content


def build_network_search_agent(model, tools):
    return {
        "name": sub_agents_content["tavily"]["name"],
        "description": sub_agents_content["tavily"]["description"],
        "system_prompt": sub_agents_content["tavily"]["system_prompt"],
        "tools": list(tools),
        "model": model,
    }
