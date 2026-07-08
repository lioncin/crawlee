from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api2.100zy.cn/v1"
DEFAULT_PROVIDER = "openai-custom"


class LLMClientError(RuntimeError):
    pass


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _read_config() -> tuple[str, str, str]:
    env_path = Path(__file__).with_name(".env")
    _load_dotenv(env_path)

    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "")

    if not api_key:
        raise LLMClientError("Missing LLM_API_KEY in environment or .env")

    return model, base_url, api_key


async def chat_completion(prompt: str, timeout_seconds: float = 30.0) -> str:
    content = prompt.strip()
    if not content:
        raise LLMClientError("prompt must not be empty")

    model, base_url, api_key = _read_config()

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": content},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    url = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMClientError(f"LLM request failed: {exc}") from exc

    data = resp.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMClientError("LLM response missing choices")

    message = choices[0].get("message") or {}
    answer = message.get("content")

    if isinstance(answer, str):
        return answer

    if isinstance(answer, list):
        parts: list[str] = []
        for item in answer:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)

    raise LLMClientError("LLM response missing message content")


def _strip_code_fence(text: str) -> str:
    content = text.strip()
    if not content.startswith("```"):
        return content

    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _candidate_from_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, dict):
        for key in ("issuer_full_name", "issuer", "company_name", "name"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()

    return None


async def extract_issuer_names_from_titles(titles: list[str], timeout_seconds: float = 30.0) -> list[str | None]:
    if not titles:
        return []

    rows = [{"index": idx, "title": title} for idx, title in enumerate(titles)]
    prompt = f"""
你是信息抽取助手。请从每个公告标题中提取发行人公司全称(issuer_full_name)。
规则：
1) 如果标题中能明确识别公司全称，返回该全称；
2) 如果无法明确识别，返回 null；
3) 仅返回 JSON，不要额外解释；
4) 返回格式必须是 JSON 数组，长度与输入一致。
推荐输出格式：
[
  {{"index": 0, "issuer_full_name": "..."}},
  {{"index": 1, "issuer_full_name": null}}
]
输入：
{json.dumps(rows, ensure_ascii=False)}
""".strip()

    raw = await chat_completion(prompt, timeout_seconds=timeout_seconds)
    cleaned = _strip_code_fence(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"Failed to parse LLM issuer JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("issuer_full_names"), list):
        data = data["issuer_full_names"]

    if not isinstance(data, list):
        raise LLMClientError("LLM issuer response must be a JSON array")

    normalized: list[str | None] = [None] * len(titles)

    has_indexed_rows = any(isinstance(row, dict) and isinstance(row.get("index"), int) for row in data)
    if has_indexed_rows:
        for row in data:
            if not isinstance(row, dict):
                continue
            idx = row.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(titles):
                continue
            normalized[idx] = _candidate_from_value(row)
        return normalized

    for idx, value in enumerate(data[: len(titles)]):
        normalized[idx] = _candidate_from_value(value)

    return normalized


def provider_config_text() -> str:
    return "\n".join(
        [
            f"model = {DEFAULT_MODEL}",
            f"model_provider = {DEFAULT_PROVIDER}",
            f"[model_providers.{DEFAULT_PROVIDER}]",
            "name = Custom OpenAI",
            f"base_url = {DEFAULT_BASE_URL}",
        ]
    )
