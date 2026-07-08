from __future__ import annotations

import asyncio
import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from llm_client import LLMClientError, chat_completion, extract_issuer_names_from_titles
from mysql_store import store_fetch_result

app = FastAPI(title="Crawlee URL Result API", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fixed target URLs. `url: "*"` will fetch all of them.
FIXED_URLS = [
    "https://www.szse.cn/disclosure/notice/company/index.html",
    "https://www.sse.com.cn/listing/renewal/ipo/",
]


class FetchRequest(BaseModel):
    url: str
    timeout_seconds: float = 20.0
    include_html: bool = False


class LLMChatRequest(BaseModel):
    prompt: str
    timeout_seconds: float = 30.0


class LLMChatResponse(BaseModel):
    content: str


class NoticeItem(BaseModel):
    date: str
    title: str
    url: str
    issuer_full_name: str | None = None
    board: str | None = None
    audit_status: str | None = None
    province: str | None = None
    industry: str | None = None
    sponsor: str | None = None
    law_firm: str | None = None
    accounting_firm: str | None = None
    update_date: str | None = None
    accept_date: str | None = None


class FetchResponse(BaseModel):
    url: str
    final_url: str
    status_code: int
    title: str | None
    html_length: int
    text: str
    items: list[NoticeItem] = Field(default_factory=list)
    html: str | None = None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_NOTICE_ITEM_RE = re.compile(
    r"var\s+curHref\s*=\s*'([^']+)'\s*;.*?var\s+curTitle\s*=\s*'([^']+)'\s*;.*?<span\s+class=\"time\">\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*</span>",
    re.IGNORECASE | re.DOTALL,
)
_JSONP_WRAP_RE = re.compile(r"^[\w$]+\((.*)\)\s*;?$", re.DOTALL)

_ISSUE_MARKET_MAP = {
    "1": "科创板",
    "2": "主板",
}

_CURR_STATUS_MAP = {
    "1": "已受理",
    "2": "已问询",
    "3": "上市委会议",
    "4": "提交注册",
    "5": "注册结果",
    "6": "中止",
    "7": "终止",
}


def extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    return re.sub(r"\s+", " ", unescape(match.group(1))).strip() or None


def extract_text(html: str) -> str:
    without_script_style = _SCRIPT_STYLE_RE.sub(" ", html)
    no_tags = _STRIP_TAGS_RE.sub(" ", without_script_style)
    decoded = unescape(no_tags)
    return re.sub(r"\s+", " ", decoded).strip()


def extract_notice_items(html: str, base_url: str) -> list[NoticeItem]:
    items: list[NoticeItem] = []
    for href, raw_title, date in _NOTICE_ITEM_RE.findall(html):
        title = re.sub(r"\s+", " ", unescape(raw_title)).strip()
        items.append(
            NoticeItem(
                date=date.strip(),
                title=title,
                url=urljoin(base_url, href.strip()),
            )
        )
    return items


def is_sse_listing_ipo(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("sse.com.cn") and parsed.path.startswith("/listing/renewal/ipo")


def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_date_yyyymmdd(value: str | None) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def get_intermediary_name(intermediary: list[dict], intermediary_type: int) -> str:
    for row in intermediary:
        if str(row.get("i_intermediaryType", "")) == str(intermediary_type):
            return str(row.get("i_intermediaryName") or "")
    return ""


def map_curr_status(row: dict) -> str:
    code = str(row.get("currStatus") or "")
    return _CURR_STATUS_MAP.get(code, code)


async def fill_missing_issuer_full_name(items: list[NoticeItem]) -> None:
    missing_indexes: list[int] = []
    missing_titles: list[str] = []

    for idx, item in enumerate(items):
        if item.issuer_full_name and item.issuer_full_name.strip():
            continue
        if not item.title or not item.title.strip():
            continue
        missing_indexes.append(idx)
        missing_titles.append(item.title.strip())

    if not missing_titles:
        return

    try:
        inferred = await extract_issuer_names_from_titles(missing_titles)
    except LLMClientError:
        return

    for idx, issuer_name in zip(missing_indexes, inferred):
        if issuer_name and issuer_name.strip():
            items[idx].issuer_full_name = issuer_name.strip()


async def fetch_sse_ipo_items(client: httpx.AsyncClient) -> list[NoticeItem]:
    params = {
        "jsonCallBack": "cb",
        "sqlId": "SH_XM_LB",
        "keyword": "",
        "issueMarketType": "1,2",
        "currStatus": "",
        "province": "",
        "csrcCode": "",
        "auditApplyDateBegin": "",
        "auditApplyDateEnd": "",
        "order": "updateDate|desc,stockAuditNum|desc",
        "isPagination": "true",
        "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.endPage": "1",
        "pageHelp.pageSize": "25",
        "pageHelp.pageNo": "1",
    }
    headers = {
        "Referer": "https://www.sse.com.cn/listing/renewal/ipo/",
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
    }

    response = await client.get("https://query.sse.com.cn/commonSoaQuery.do", params=params, headers=headers)
    body = response.text.strip()

    match = _JSONP_WRAP_RE.match(body)
    if not match:
        return []

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    records = payload.get("pageHelp", {}).get("data", [])
    items: list[NoticeItem] = []

    for row in records:
        title = str(row.get("stockAuditName") or "").strip()
        if not title:
            continue

        audit_id = str(row.get("stockAuditNum") or "").strip()
        detail_url = (
            f"https://www.sse.com.cn/listing/renewal/ipo/index_listing_detail.shtml?auditId={audit_id}"
            if audit_id
            else "https://www.sse.com.cn/listing/renewal/ipo/"
        )

        stock_issuer = (row.get("stockIssuer") or [{}])[0] if isinstance(row.get("stockIssuer"), list) else {}
        intermediary = row.get("intermediary") or []

        update_date = normalize_date_yyyymmdd(row.get("updateDate"))
        accept_date = normalize_date_yyyymmdd(row.get("auditApplyDate"))

        items.append(
            NoticeItem(
                date=update_date,
                title=title,
                url=detail_url,
                issuer_full_name=str(stock_issuer.get("s_issueCompanyFullName") or "") or None,
                board=_ISSUE_MARKET_MAP.get(str(row.get("issueMarketType") or ""), None),
                audit_status=map_curr_status(row) or None,
                province=str(stock_issuer.get("s_province") or "") or None,
                industry=str(stock_issuer.get("s_csrcCodeDesc") or "") or None,
                sponsor=get_intermediary_name(intermediary, 1) or None,
                law_firm=get_intermediary_name(intermediary, 3) or None,
                accounting_firm=get_intermediary_name(intermediary, 2) or None,
                update_date=update_date or None,
                accept_date=accept_date or None,
            )
        )

    return items


async def fetch_one_url(
    client: httpx.AsyncClient,
    target_url: str,
    include_html: bool,
) -> FetchResponse:
    response = await client.get(target_url)

    html = response.text
    final_url = str(response.url)

    items = extract_notice_items(html, final_url)
    if not items and is_sse_listing_ipo(final_url):
        items = await fetch_sse_ipo_items(client)

    if items:
        await fill_missing_issuer_full_name(items)

    if items:
        lines = []
        for item in items:
            lines.append(
                " | ".join(
                    [
                        item.update_date or item.date,
                        item.issuer_full_name or item.title,
                        item.board or "",
                        item.audit_status or "",
                        item.province or "",
                        item.industry or "",
                        item.sponsor or "",
                        item.law_firm or "",
                        item.accounting_firm or "",
                        item.accept_date or "",
                        item.url,
                    ]
                )
            )
        text = "\n".join(lines)
    else:
        text = extract_text(html)

    return FetchResponse(
        url=target_url,
        final_url=final_url,
        status_code=response.status_code,
        title=extract_title(html),
        html_length=len(html),
        text=text,
        items=items,
        html=html if include_html else None,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/fetch", response_model=FetchResponse | dict[str, FetchResponse])
async def fetch_url(payload: FetchRequest) -> FetchResponse | dict[str, FetchResponse]:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="url must not be empty")

    timeout = httpx.Timeout(payload.timeout_seconds)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            if url == "*":
                results: dict[str, FetchResponse] = {}
                for target in FIXED_URLS:
                    results[target] = await fetch_one_url(client, target, payload.include_html)

                await asyncio.to_thread(
                    store_fetch_result,
                    url,
                    {k: v.model_dump(mode="json") for k, v in results.items()},
                )
                return results

            if not is_valid_http_url(url):
                raise HTTPException(status_code=422, detail="url must be a valid http/https URL or '*'")

            single_result = await fetch_one_url(client, url, payload.include_html)
            await asyncio.to_thread(
                store_fetch_result,
                url,
                single_result.model_dump(mode="json"),
            )
            return single_result
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist result to MySQL: {exc}") from exc

@app.post("/llm/chat", response_model=LLMChatResponse)
async def llm_chat(payload: LLMChatRequest) -> LLMChatResponse:
    try:
        content = await chat_completion(payload.prompt, timeout_seconds=payload.timeout_seconds)
        return LLMChatResponse(content=content)
    except LLMClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
