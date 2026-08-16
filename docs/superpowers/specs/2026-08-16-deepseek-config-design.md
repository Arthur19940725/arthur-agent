# DeepSeek 配置迁移设计

## 目标

将项目的通义千问/OpenAI 环境变量统一迁移为 DeepSeek 专用命名，并使用官方 OpenAI 兼容接口。

## 配置

- `DEEPSEEK_API_KEY`：保留现有 API Key 值。
- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `LLM_DEEPSEEK_MODEL=deepseek-v4-flash`

删除 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和全部 `LLM_QWEN*` 配置。

## 代码与文档

`agent/llm.py` 显式向 LangChain 传入 DeepSeek API Key、Base URL 和模型名；`.env.example` 与 README 同步展示新变量。服务重启后通过导入模型配置验证迁移结果。

## 错误处理与验证

缺少配置时，模型初始化应明确失败；验证时不发送真实请求，避免消耗额度。迁移不保留旧变量回退，防止错误配置被静默使用。
