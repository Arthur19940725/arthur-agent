from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain.chat_models import init_chat_model

_VALID_THINKING = {"adaptive", "enabled", "disabled"}


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str
    base_url: str
    flash_model: str
    pro_model: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeepSeekSettings:
        values = os.environ if environ is None else environ
        required = (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "LLM_DEEPSEEK_MODEL",
            "LLM_DEEPSEEK_PRO",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"missing DeepSeek configuration: {', '.join(missing)}")
        return cls(
            api_key=values["DEEPSEEK_API_KEY"],
            base_url=values["DEEPSEEK_BASE_URL"],
            flash_model=values["LLM_DEEPSEEK_MODEL"],
            pro_model=values["LLM_DEEPSEEK_PRO"],
        )


@dataclass(frozen=True)
class ModelBundle:
    flash: Any
    pro: Any


def _init_deepseek_model(
    model_name: str,
    *,
    api_key: str,
    base_url: str,
    thinking: str,
    temperature: float,
    reasoning_effort: str | None = None,
):
    if thinking not in _VALID_THINKING:
        raise ValueError(
            f"thinking.type must be one of {sorted(_VALID_THINKING)}, got {thinking!r}"
        )
    extra_body: dict[str, Any] = {"thinking": {"type": thinking}}
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        extra_body=extra_body,
    )


def create_model_bundle(settings: DeepSeekSettings | None = None) -> ModelBundle:
    settings = settings or DeepSeekSettings.from_env()
    return ModelBundle(
        flash=_init_deepseek_model(
            settings.flash_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            thinking="disabled",
            temperature=0.3,
        ),
        pro=_init_deepseek_model(
            settings.pro_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            thinking="enabled",
            reasoning_effort="high",
            temperature=0.6,
        ),
    )
