from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from serve.model_secrets import encrypted_model_key


DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_TIMEOUT_SECONDS = 15
DEFAULT_MODEL_MAX_TOKENS = 1400


def _load_local_env() -> None:
    for filename in (".env", ".env.local"):
        path = os.getcwd() + "/" + filename
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


_load_local_env()


def model_metadata() -> dict:
    key = bool(_api_key())
    base_url = _base_url()
    model = _model_name()
    return {
        "model_provider": _model_provider(base_url, model) if key else "fallback",
        "model_name": model if key else "deterministic-template",
        "model_mode": "live" if key else "fallback",
        "model_reasoning": "enabled" if key and _thinking_enabled(base_url, model) else "not_requested",
        "model_timeout_seconds": _timeout_seconds(),
        "model_max_tokens": _max_tokens(base_url, model),
    }


def complete_json(system_prompt: str, user_payload: dict[str, Any], timeout: int | None = None) -> dict | None:
    result = complete_json_with_diagnostics(system_prompt, user_payload, timeout=timeout)
    return result.get("payload") if result.get("ok") else None


def complete_json_with_diagnostics(system_prompt: str, user_payload: dict[str, Any], timeout: int | None = None) -> dict:
    started = time.perf_counter()
    api_key = _api_key()
    if not api_key:
        return _model_result(started, timeout, None, "missing_api_key")
    base_url = _base_url()
    model = _model_name()
    request_timeout = timeout or _timeout_seconds()
    body = {
        "model": model,
        "temperature": 0.15,
        "max_tokens": _max_tokens(base_url, model),
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt + "\n只输出一个合法 JSON 对象，不要输出 Markdown 代码块。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    if _thinking_enabled(base_url, model):
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = os.getenv("MODEL_REASONING_EFFORT", "medium")
    attempts = _retry_count() + 1
    last_error = "unknown_error"
    for attempt in range(attempts):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
                json=body,
                timeout=request_timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_json_object(content)
            if parsed:
                return _model_result(started, request_timeout, parsed, None)
            last_error = "invalid_json"
        except requests.Timeout:
            last_error = "timeout"
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", "unknown")
            last_error = f"http_{status}"
        except requests.ConnectionError:
            last_error = "connection_error"
        except Exception as exc:
            last_error = type(exc).__name__
        if attempt < attempts - 1:
            time.sleep(1)
    return _model_result(started, request_timeout, None, last_error)


def _api_key() -> str:
    return os.getenv("MODEL_API_KEY", "").strip() or encrypted_model_key()


def _base_url() -> str:
    return os.getenv("MODEL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _model_name() -> str:
    return os.getenv("MODEL_NAME", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _model_provider(base_url: str, model: str) -> str:
    combined = f"{base_url} {model}".lower()
    if "deepseek" in combined:
        return "deepseek"
    if "agnes" in combined:
        return "agnes-ai"
    return "openai-compatible"


def _thinking_enabled(base_url: str, model: str) -> bool:
    combined = f"{base_url} {model}".lower()
    raw = os.getenv("MODEL_THINKING_ENABLED", "0").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return "deepseek" in combined and model.strip().lower() == "deepseek-v4-pro"
    return "deepseek" in combined and model.strip().lower() == "deepseek-v4-pro"


def _max_tokens(base_url: str, model: str) -> int:
    default = DEFAULT_MODEL_MAX_TOKENS
    raw = os.getenv("MODEL_MAX_TOKENS", "").strip()
    if not raw:
        return default
    try:
        return max(256, int(raw))
    except ValueError:
        return default


def _timeout_seconds() -> int:
    raw = os.getenv("MODEL_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_MODEL_TIMEOUT_SECONDS
    try:
        return max(3, int(raw))
    except ValueError:
        return DEFAULT_MODEL_TIMEOUT_SECONDS


def _retry_count() -> int:
    raw = os.getenv("MODEL_RETRY_COUNT", "0").strip()
    try:
        return max(0, min(2, int(raw)))
    except ValueError:
        return 0


def _model_result(started: float, timeout: int | None, payload: dict | None, error_type: str | None) -> dict:
    return {
        "ok": payload is not None,
        "payload": payload,
        "error_type": error_type,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "timeout_seconds": timeout,
    }


def _parse_json_object(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None
