from dotenv import load_dotenv, find_dotenv
import os
from langchain.chat_models import init_chat_model

# 加载配置文件
# find_dotenv() 确保找到 .env文件 递归查询当前项目文件夹
load_dotenv(find_dotenv())

# DeepSeek Chat Completions：thinking.type 只能是 adaptive / enabled / disabled。
# 思考强度用 extra_body.reasoning_effort（high / max），不要写进 thinking.type。
_VALID_THINKING = {"adaptive", "enabled", "disabled"}


def _init_deepseek_model(
    model_name: str,
    *,
    thinking: str,
    temperature: float,
    reasoning_effort: str | None = None,
):
    if thinking not in _VALID_THINKING:
        raise ValueError(
            f"thinking.type must be one of {sorted(_VALID_THINKING)}, got {thinking!r}"
        )
    extra_body = {"thinking": {"type": thinking}}
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        temperature=temperature,
        extra_body=extra_body,
    )


# Flash：子智能体与单步工具调用，关闭思考以降低延迟。
model = _init_deepseek_model(
    os.environ["LLM_DEEPSEEK_MODEL"],
    thinking="disabled",
    temperature=0.3,
)
# Pro：主控路由、综合与审批规划。
pro_model = _init_deepseek_model(
    os.environ["LLM_DEEPSEEK_PRO"],
    thinking="enabled",
    reasoning_effort="high",
    temperature=0.6,
)
