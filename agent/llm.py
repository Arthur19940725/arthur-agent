from dotenv import load_dotenv,find_dotenv
import os
from langchain.chat_models import init_chat_model

# 加载配置文件
# find_dotenv() 确保找到 .env文件 递归查询当前项目文件夹
load_dotenv(find_dotenv())

def _init_deepseek_model(model_name: str):
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
    )


model = _init_deepseek_model(os.environ["LLM_DEEPSEEK_MODEL"])
pro_model = _init_deepseek_model(os.environ["LLM_DEEPSEEK_PRO"])