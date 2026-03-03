from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


class LLMError(RuntimeError):
    pass


def _client_from_env(*, base_url: str | None, api_key: str | None) -> OpenAI:
    ark_api_key = os.getenv("ARK_API_KEY") or os.getenv("DOUBAO_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    api_key_final = (api_key or "").strip() or ark_api_key or openai_api_key
    if not api_key_final:
        raise LLMError("missing api key: set ARK_API_KEY (Doubao) or OPENAI_API_KEY")

    base_url_final = (
        base_url
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("ARK_BASE_URL")
        or os.getenv("DOUBAO_BASE_URL")
    )
    if base_url_final is None and ark_api_key:
        base_url_final = "https://ark.cn-beijing.volces.com/api/v3"
    if base_url_final:
        return OpenAI(api_key=api_key_final, base_url=base_url_final)
    return OpenAI(api_key=api_key_final)


def chat_completion(
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.0,
    base_url: str | None = None,
    api_key: str | None = None,
) -> str:
    client = _client_from_env(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    content = resp.choices[0].message.content
    return content or ""
