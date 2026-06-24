from __future__ import annotations

import json
import os
from typing import Any

import requests


DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_BASE_URL = "https://api.apimart.ai/v1"


def model_metadata() -> dict:
    key = bool(os.getenv("APIMART_API_KEY"))
    return {
        "model_provider": "apimart" if key else "fallback",
        "model_name": os.getenv("APIMART_MODEL", DEFAULT_MODEL) if key else "deterministic-template",
        "model_mode": "live" if key else "fallback",
    }


def complete_json(system_prompt: str, user_payload: dict[str, Any], timeout: int = 30) -> dict | None:
    api_key = os.getenv("APIMART_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("APIMART_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.getenv("APIMART_MODEL", DEFAULT_MODEL)
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={
                "model": model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None
