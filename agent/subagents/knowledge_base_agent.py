from agent.prompts import sub_agents_content


def build_knowledge_base_agent(model, tools):
    return {
        "name": sub_agents_content["ragflow"]["name"],
        "description": sub_agents_content["ragflow"]["description"],
        "system_prompt": sub_agents_content["ragflow"]["system_prompt"],
        "tools": list(tools),
        "model": model,
    }
