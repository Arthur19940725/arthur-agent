# DeepSeek Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目的大模型配置统一迁移到 DeepSeek 官方 OpenAI 兼容接口。

**Architecture:** 环境文件只暴露 DeepSeek 专用变量，`agent/llm.py` 显式把这些变量传给 LangChain。示例配置和 README 与运行时代码保持一致。

**Tech Stack:** Python、python-dotenv、LangChain、DeepSeek OpenAI-compatible API

## Global Constraints

- 使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`LLM_DEEPSEEK_MODEL`。
- Base URL 固定为 `https://api.deepseek.com`，默认模型为 `deepseek-v4-flash`。
- 不保留旧的 `OPENAI_*` 或 `LLM_QWEN*` 回退。
- 不发送真实模型请求进行验证，避免消耗额度。

---

### Task 1: 迁移运行时与文档配置

**Files:**
- Modify: `.env`
- Modify: `.env.example:10-18`
- Modify: `agent/llm.py:1-12`
- Modify: `README.md:94-124`

**Interfaces:**
- Consumes: DeepSeek OpenAI-compatible API 配置。
- Produces: LangChain `model` 实例，读取三个 DeepSeek 环境变量。

- [ ] **Step 1: 验证旧变量仍存在**

Run:
```powershell
rg "OPENAI_API_KEY|OPENAI_BASE_URL|LLM_QWEN" agent README.md .env.example
```

Expected: 输出 `agent/llm.py`、README 和 `.env.example` 中的旧变量。

- [ ] **Step 2: 修改运行时代码**

将 `agent/llm.py` 的初始化改为：
```python
model = init_chat_model(
    model=os.environ["LLM_DEEPSEEK_MODEL"],
    model_provider="openai",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.environ["DEEPSEEK_BASE_URL"],
)
```

- [ ] **Step 3: 同步环境文件与 README**

统一配置内容：
```env
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=your-deepseek-api-key
LLM_DEEPSEEK_MODEL=deepseek-v4-flash
```

`.env` 保留用户现有 Key 的值，只重命名键；示例和 README 使用占位值。

- [ ] **Step 4: 验证配置加载且旧变量已清除**

Run:
```powershell
python -c "from dotenv import dotenv_values; c=dotenv_values('.env'); assert all(c.get(k) for k in ('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','LLM_DEEPSEEK_MODEL')); assert not any(k.startswith('LLM_QWEN') or k.startswith('OPENAI_') for k in c); print('DeepSeek config OK')"
rg "OPENAI_API_KEY|OPENAI_BASE_URL|LLM_QWEN" agent README.md .env.example
```

Expected: 第一条输出 `DeepSeek config OK`；第二条无输出。

- [ ] **Step 5: 验证 Python 文件**

Run:
```powershell
python -m py_compile agent/llm.py
```

Expected: 退出码为 0 且无输出。
