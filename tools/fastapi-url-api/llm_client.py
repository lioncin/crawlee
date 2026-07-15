from __future__ import annotations

import json
import logging
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


logger = logging.getLogger(__name__)


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


def _normalize_recognition_json_shape(obj: dict[str, Any]) -> dict[str, Any]:
    company_profile = obj.get("company_profile")
    company_registry_profile = obj.get("company_registry_profile")
    company_certificate = obj.get("company_certificate")

    if not isinstance(company_profile, dict):
        company_profile = {}
    if not isinstance(company_registry_profile, dict):
        company_registry_profile = {}
    if isinstance(company_certificate, dict):
        company_certificate = [company_certificate]
    if not isinstance(company_certificate, list):
        company_certificate = []

    return {
        "company_profile": company_profile,
        "company_registry_profile": company_registry_profile,
        "company_certificate": [x for x in company_certificate if isinstance(x, dict)],
    }


def _merge_non_empty_fields(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in fallback.items():
        if key not in merged:
            merged[key] = value
            continue

        cur = merged.get(key)
        if cur in (None, "", [], {}):
            merged[key] = value

    return merged


def _is_all_empty_recognition(parsed: dict[str, Any]) -> bool:
    if not isinstance(parsed, dict):
        return True

    profile = parsed.get("company_profile") if isinstance(parsed.get("company_profile"), dict) else {}
    registry = parsed.get("company_registry_profile") if isinstance(parsed.get("company_registry_profile"), dict) else {}
    certs = parsed.get("company_certificate") if isinstance(parsed.get("company_certificate"), list) else []

    def has_non_empty(d: dict[str, Any]) -> bool:
        for value in d.values():
            if value not in (None, "", [], {}):
                return True
        return False

    has_cert = any(isinstance(x, dict) and any(v not in (None, "", [], {}) for v in x.values()) for x in certs)
    return not has_non_empty(profile) and not has_non_empty(registry) and not has_cert


def _to_company_info(parsed: dict[str, Any]) -> dict[str, Any]:
    profile = parsed.get("company_profile") if isinstance(parsed.get("company_profile"), dict) else {}
    registry = parsed.get("company_registry_profile") if isinstance(parsed.get("company_registry_profile"), dict) else {}
    certs = parsed.get("company_certificate") if isinstance(parsed.get("company_certificate"), list) else []

    merged_profile = _merge_non_empty_fields(profile, registry)

    return {
        "issuer_full_name": merged_profile.get("issuer_full_name") or merged_profile.get("company_name"),
        "company_name": merged_profile.get("company_name") or merged_profile.get("issuer_full_name"),
        "contact_name": merged_profile.get("contact_name") or merged_profile.get("contact") or merged_profile.get("contact_person"),
        "phone": merged_profile.get("phone") or merged_profile.get("telephone") or merged_profile.get("mobile"),
        "email": merged_profile.get("email") or merged_profile.get("mail"),
        "employee_count": merged_profile.get("employee_count") or merged_profile.get("employees_text") or merged_profile.get("staff_count"),
        "operating_revenue": merged_profile.get("operating_revenue") or merged_profile.get("revenue_text") or merged_profile.get("annual_revenue"),
        "insured_count": merged_profile.get("insured_count") or merged_profile.get("insured_persons") or merged_profile.get("insured_num"),
        "certificates": [x for x in certs if isinstance(x, dict)],
    }


async def recognize_company_from_images(image_urls: list[str], timeout_seconds: float = 420.0) -> dict[str, Any]:
    urls = [str(u).strip() for u in image_urls if str(u).strip()]
    if len(urls) == 0:
        raise LLMClientError("image_urls must not be empty")

    model, base_url, api_key = _read_vision_config()

    prompt = (
        "下面这些图片是同一家公司信息页面的分段截图。请合并理解并只返回一个 JSON。\\n\\n"
        "【必须遵守】\\n"
        "1) 只能输出 JSON，不允许 markdown/代码块/解释文字。\\n"
        "2) 顶层 key 只能是 company_profile、company_registry_profile、company_certificate。\\n"
        "3) 字段名必须使用 snake_case，并严格使用给定字段名。\\n"
        "4) 缺失值填 null；日期用 YYYY-MM-DD；company_certificate 必须是数组。\\n\\n"
        "输出模板：\\n"
        "{\\n"
        '  "company_profile": {\\n'
        '    "company_name": null, "status": null, "brand_logo": null, "stock_code": null, "stock_market": null,\\n'
        '    "unified_social_credit_code": null, "legal_representative": null, "registered_capital": null, "establishment_date": null,\\n'
        '    "phone": null, "email": null, "website": null, "address": null,\\n'
        '    "industry": null, "scale": null, "employees_text": null, "revenue_text": null,\\n'
        '    "enterprise_score_text": null, "tech_innovation_score_text": null,\\n'
        '    "tags": [], "scores": {}, "basic_info": {}, "contact_info": {}, "business_data": {}, "ai_summary": null\\n'
        '  },\\n'
        '  "company_registry_profile": {\\n'
        '    "unified_social_credit_code": null, "company_name": null, "registration_status": null, "establishment_date": null,\\n'
        '    "legal_representative": null, "registered_capital": null, "paid_in_capital": null, "organization_code": null,\\n'
        '    "registration_number": null, "taxpayer_id": null, "company_type": null, "business_term": null, "taxpayer_qualification": null,\\n'
        '    "insured_persons": null, "branch_insured_persons": null, "approval_date": null, "region": null,\\n'
        '    "registration_authority": null, "import_export_code": null, "industry": null, "english_name": null,\\n'
        '    "registered_address": null, "former_names": [], "business_scope": null\\n'
        '  },\\n'
        '  "company_certificate": [\\n'
        '    {\\n'
        '      "certificate_no": null, "certificate_status": null, "issue_date": null, "expiry_date": null,\\n'
        '      "first_issue_date": null, "report_date": null, "supervision_count": null, "recertification_count": null,\\n'
        '      "certification_project": null, "accreditation_mark": null, "certification_scope": null, "certification_basis": null,\\n'
        '      "covers_multiple_sites": null, "is_sub_certificate": null, "parent_certificate_no": null\\n'
        '    }\\n'
        '  ]\\n'
        "}"
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
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
        if not isinstance(loaded, dict):
            raise LLMClientError("Vision LLM must return a JSON object")
        parsed = _normalize_recognition_json_shape(clean_year_suffix(loaded))
    except json.JSONDecodeError:
        raise LLMClientError("Vision LLM returned non-JSON content")

    logger.info("recognize_company_from_images raw_text(head): %s", (raw_text or "")[:500])

    retry_used = False
    warning = ""

    if _is_all_empty_recognition(parsed):
        retry_used = True
        retry_prompt = (
            "请重新读取这些图片，并尽量提取公司基础信息。\n"
            "只输出 JSON，顶层必须包含 company_profile、company_registry_profile、company_certificate。\n"
            "如果无法确认具体值，也请尽量填写截图中可见的公司名、员工人数、营收、参保人数等字段。"
        )
        retry_content: list[dict[str, Any]] = [{"type": "text", "text": retry_prompt}]
        retry_content.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
        retry_payload = {
            "model": model,
            "messages": [{"role": "user", "content": retry_content}],
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=timeout_seconds, write=30.0, pool=20.0)) as client:
                retry_resp = await client.post(url, headers=headers, json=retry_payload)
            retry_resp.raise_for_status()
            retry_data = retry_resp.json()
            retry_choices = retry_data.get("choices")
            retry_message = retry_choices[0].get("message") if isinstance(retry_choices, list) and retry_choices else {}
            retry_answer = retry_message.get("content") if isinstance(retry_message, dict) else ""
            retry_text = retry_answer if isinstance(retry_answer, str) else ""
            logger.info("recognize_company_from_images retry_raw_text(head): %s", (retry_text or "")[:500])

            retry_loaded = json.loads(_strip_code_fence(retry_text))
            if isinstance(retry_loaded, dict):
                retry_parsed = _normalize_recognition_json_shape(clean_year_suffix(retry_loaded))
                if not _is_all_empty_recognition(retry_parsed):
                    parsed = retry_parsed
                    raw_text = retry_text
                else:
                    warning = "LLM returned empty structured fields twice; check model vision capability or image readability"
            else:
                warning = "LLM retry returned non-object JSON"
        except Exception as exc:
            warning = f"LLM retry failed: {exc}"

    company_info = _to_company_info(parsed)

    return {
        "model": model,
        "raw_text": raw_text,
        "parsed": parsed,
        "company_info": company_info,
        "warning": warning,
        "retry_used": retry_used,
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


def _notice_fields_from_value(value: object) -> dict[str, str | None]:
    issuer_full_name: str | None = None
    audit_status: str | None = None

    if isinstance(value, dict):
        for key in ("issuer_full_name", "issuer", "company_name", "name"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                issuer_full_name = raw.strip()
                break

        raw_status = value.get("audit_status")
        if isinstance(raw_status, str) and raw_status.strip():
            audit_status = raw_status.strip()

    return {
        "issuer_full_name": issuer_full_name,
        "audit_status": audit_status,
    }


async def extract_notice_fields_from_titles(rows: list[dict[str, object]], timeout_seconds: float = 30.0) -> list[dict[str, str | None]]:
    if not rows:
        return []

    input_rows = []
    for idx, row in enumerate(rows):
        input_rows.append(
            {
                "index": idx,
                "title": str(row.get("title") or ""),
                "current_audit_status": str(row.get("audit_status") or ""),
            }
        )

    prompt = f"""
你是信息抽取助手。请从每条记录中只提取两个字段：发行人公司全称(issuer_full_name)和审核状态(audit_status)。
规则：
1) issuer_full_name：从 title 中识别发行人公司全称；如果无法明确识别，返回 null；
2) audit_status：优先使用输入里的 current_audit_status，并规范为 已受理、已问询、上市委会议、提交注册、注册结果、中止、终止 之一；如果无法明确识别，返回 null；
3) 仅返回 JSON，不要额外解释；
4) 返回格式必须是 JSON 数组，长度与输入一致；
5) 每个元素只允许包含 index、issuer_full_name、audit_status。
推荐输出格式：
[
  {{"index": 0, "issuer_full_name": "...", "audit_status": "已问询"}},
  {{"index": 1, "issuer_full_name": null, "audit_status": null}}
]
输入：
{json.dumps(input_rows, ensure_ascii=False)}
""".strip()

    raw = await chat_completion(prompt, timeout_seconds=timeout_seconds)
    cleaned = _strip_code_fence(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"Failed to parse LLM notice fields JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]

    if not isinstance(data, list):
        raise LLMClientError("LLM notice fields response must be a JSON array")

    normalized: list[dict[str, str | None]] = [
        {"issuer_full_name": None, "audit_status": None} for _ in rows
    ]

    has_indexed_rows = any(isinstance(row, dict) and isinstance(row.get("index"), int) for row in data)
    if has_indexed_rows:
        for row in data:
            if not isinstance(row, dict):
                continue
            idx = row.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(rows):
                continue
            normalized[idx] = _notice_fields_from_value(row)
        return normalized

    for idx, value in enumerate(data[: len(rows)]):
        normalized[idx] = _notice_fields_from_value(value)

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
