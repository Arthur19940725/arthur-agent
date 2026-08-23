from agent.prompts import sub_agents_content


def build_database_query_agent(model, tools):
    return {
        "name": sub_agents_content["db"]["name"],
        "description": sub_agents_content["db"]["description"],
        "system_prompt": sub_agents_content["db"]["system_prompt"],
        "tools": list(tools),
        "model": model,
    }
