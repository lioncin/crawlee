from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api2.100zy.cn/v1"
DEFAULT_PROVIDER = "openai-custom"

DEFAULT_VISION_MODEL = "qwen3.7-plus"
DEFAULT_VISION_BASE_URL = "https://llm-8pbtfdcbylx7i51t.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

YEAR_SUFFIX_PATTERN = re.compile(r"\s*[（(]\s*\d{4}年(?:报)?\s*[)）]\s*$")


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


def _read_vision_config() -> tuple[str, str, str]:
    env_path = Path(__file__).with_name(".env")
    _load_dotenv(env_path)

    model = os.getenv("DASHSCOPE_MODEL", DEFAULT_VISION_MODEL)
    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_VISION_BASE_URL).rstrip("/")
    api_key = os.getenv("DASHSCOPE_API_KEY", "")

    if not api_key:
        raise LLMClientError("Missing DASHSCOPE_API_KEY in environment or .env")

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
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=timeout_seconds, write=30.0, pool=20.0)) as client:
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


def clean_year_suffix(value: Any) -> Any:
    if isinstance(value, str):
        return YEAR_SUFFIX_PATTERN.sub("", value).strip()
    if isinstance(value, list):
        return [clean_year_suffix(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_year_suffix(v) for k, v in value.items()}
    return value


async def recognize_company_from_images(image_urls: list[str], timeout_seconds: float = 180.0) -> dict[str, Any]:
    urls = [str(u).strip() for u in image_urls if str(u).strip()]
    if len(urls) == 0:
        raise LLMClientError("image_urls must not be empty")

    model, base_url, api_key = _read_vision_config()

    prompt = (
        "下面这些图片是同一家公司信息页面的分段截图，不是多家公司。"
        "请把所有图片合并理解后，输出尽可能完整的识别结果。"
        "不要只返回固定字段，不要省略可识别信息。\\n\\n"
        "严格规则：\\n"
        "1) 只输出一个 JSON，不要输出额外说明文字；\\n"
        "2) 字段值里禁止出现‘图片1’、‘图片2’、‘两者’、‘见上图’等来源占位词；\\n"
        "3) 如果某字段无法确认，值必须是 null，不要编造；\\n"
        "4) 来源信息只能放在 evidence 中，不能放到业务字段值里；\\n"
        "5) 删除无意义字段（例如 english_name=图片2）；\\n"
        "6) 字段值不要包含年份括号后缀，例如把‘2271 (2025年)’输出为‘2271’。\\n\\n"
        "JSON 输出结构建议：\\n"
        "{\\n"
        '  "company_info": { ... 所有可识别公司字段 ... },\\n'
        '  "evidence": { ... 仅记录字段来源: image1/image2/both ... },\\n'
        '  "full_ocr_text": "多图完整文本",\\n'
        '  "uncertain_items": [ ... 不确定字段 ... ]\\n'
        "}"
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    url = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=timeout_seconds, write=30.0, pool=20.0)) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMClientError(f"Vision LLM request timeout after {timeout_seconds}s: {exc!r}") from exc
    except httpx.HTTPError as exc:
        raise LLMClientError(f"Vision LLM request transport error: {exc!r}") from exc

    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = json.dumps(body, ensure_ascii=False)
        except Exception:
            detail = (resp.text or "").strip()

        if len(detail) > 600:
            detail = detail[:600] + "..."

        raise LLMClientError(
            f"Vision LLM HTTP {resp.status_code} from {url}. Response: {detail or '<empty>'}"
        )

    data = resp.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMClientError("Vision LLM response missing choices")

    message = choices[0].get("message") or {}
    answer = message.get("content")
    raw_text = answer if isinstance(answer, str) else ""

    parsed: dict[str, Any] | None = None
    cleaned_text = _strip_code_fence(raw_text)
    try:
        loaded = json.loads(cleaned_text)
        if isinstance(loaded, dict):
            parsed = clean_year_suffix(loaded)
    except json.JSONDecodeError:
        parsed = None

    return {
        "model": model,
        "raw_text": raw_text,
        "parsed": parsed,
        "image_urls": urls,
    }


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
