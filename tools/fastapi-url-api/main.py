from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx
import oss2
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from llm_client import LLMClientError, chat_completion, extract_issuer_names_from_titles, recognize_company_from_images
from mysql_store import load_latest_results_from_mysql, save_issuer_recognition_result, store_fetch_result

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

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
    "https://www2.hkexnews.hk/New-Listings/New-Listing-Information/Main-Board?sc_lang=zh-HK",
]

OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME", "")
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")
OSS_PUBLIC_BASE_URL = os.getenv("OSS_PUBLIC_BASE_URL", "").rstrip("/")


class FetchRequest(BaseModel):
    url: str
    timeout_seconds: float = 20.0
    include_html: bool = False


class LLMChatRequest(BaseModel):
    prompt: str
    timeout_seconds: float = 30.0


class LLMChatResponse(BaseModel):
    content: str


class IssuerRecognitionRequest(BaseModel):
    source_url: str
    notice_url: str
    issuer_name: str
    image_urls: list[str] = Field(default_factory=list)
    timeout_seconds: float = 180.0


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
_HKEX_UPDATED_DATE_RE = re.compile(r"更新日期:\s*([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日")
_HKEX_TABLE_BODY_RE = re.compile(
    r"<table[^>]*class=\"[^\"]*table-mobile-list[^\"]*\"[^>]*>.*?<tbody>(.*?)</tbody>",
    re.IGNORECASE | re.DOTALL,
)
_HKEX_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HKEX_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_HKEX_LINK_RE = re.compile(r"href\s*=\s*[\"\x27]([^\"\x27]+)[\"\x27]", re.IGNORECASE)
_HKEX_DATE_IN_URL_RE = re.compile(r"/(20[0-9]{2})/([01][0-9])([0-3][0-9])/")

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

_IMAGE_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

def ensure_oss_ready() -> tuple[oss2.Bucket, str]:
    missing = [
        key
        for key, val in (
            ("OSS_BUCKET_NAME", OSS_BUCKET_NAME),
            ("OSS_ACCESS_KEY_ID", OSS_ACCESS_KEY_ID),
            ("OSS_ACCESS_KEY_SECRET", OSS_ACCESS_KEY_SECRET),
        )
        if not val
    ]
    if missing:
        raise HTTPException(status_code=500, detail=f"OSS config missing: {', '.join(missing)}")

    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)

    if OSS_PUBLIC_BASE_URL:
        public_base = OSS_PUBLIC_BASE_URL
    else:
        endpoint_host = OSS_ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
        public_base = f"https://{OSS_BUCKET_NAME}.{endpoint_host}"

    return bucket, public_base


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

def is_hkex_new_listing_info(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("hkexnews.hk") and parsed.path.startswith("/New-Listings/New-Listing-Information/Main-Board")


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

def clean_html_fragment(fragment: str) -> str:
    no_tags = _STRIP_TAGS_RE.sub(" ", fragment)
    decoded = unescape(no_tags).replace("\xa0", " ")
    return re.sub(r"\s+", " ", decoded).strip()


def parse_hkex_updated_date(html: str) -> str:
    match = _HKEX_UPDATED_DATE_RE.search(html)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def parse_hkex_date_from_url(url: str) -> str:
    match = _HKEX_DATE_IN_URL_RE.search(url)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def extract_hkex_main_board_items(html: str, base_url: str) -> list[NoticeItem]:
    table_match = _HKEX_TABLE_BODY_RE.search(html)
    if not table_match:
        return []

    updated_date = parse_hkex_updated_date(html)
    rows_html = table_match.group(1)
    rows = _HKEX_ROW_RE.findall(rows_html)

    document_types = ("新上市公告", "招股章程", "股份配發結果")
    items: list[NoticeItem] = []
    seen_links: set[str] = set()

    for row_html in rows:
        cells = _HKEX_CELL_RE.findall(row_html)
        if len(cells) < 5:
            continue

        stock_code = clean_html_fragment(cells[0])
        stock_name = clean_html_fragment(cells[1])
        issuer_name = stock_name or None

        for doc_type, doc_cell in zip(document_types, cells[2:5]):
            for raw_href in _HKEX_LINK_RE.findall(doc_cell):
                href = unescape(raw_href).strip()
                if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                    continue
                full_url = urljoin(base_url, href)
                if full_url in seen_links:
                    continue
                seen_links.add(full_url)

                date = parse_hkex_date_from_url(full_url) or updated_date
                display_name = stock_name or stock_code or "未知公司"
                if stock_code:
                    title = f"{display_name}({stock_code}) {doc_type}"
                else:
                    title = f"{display_name} {doc_type}"

                items.append(
                    NoticeItem(
                        date=date,
                        title=title,
                        url=full_url,
                        issuer_full_name=issuer_name,
                        board="港交所主板",
                        audit_status=doc_type,
                        update_date=updated_date or None,
                    )
                )

    return items


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
    if not items and is_hkex_new_listing_info(final_url):
        items = extract_hkex_main_board_items(html, final_url)

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


@app.post("/upload/image")
async def upload_images_to_oss(
    files: list[UploadFile] = File(...),
) -> dict[str, list[dict[str, str]]]:
    if not files:
        raise HTTPException(status_code=422, detail="No file uploaded")

    bucket, public_base = ensure_oss_ready()
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    uploaded: list[dict[str, str]] = []

    try:
        for f in files:
            content_type = (f.content_type or "").lower()
            if not content_type.startswith("image/"):
                continue

            raw_name = os.path.basename(f.filename or "")
            ext = os.path.splitext(raw_name)[1].lower()
            if not ext:
                ext = _IMAGE_EXT_BY_CONTENT_TYPE.get(content_type, ".png")

            object_name = f"{date_part}/{uuid.uuid4().hex}{ext}"
            content = await f.read()
            result = await asyncio.to_thread(bucket.put_object, object_name, content)
            if result.status != 200:
                raise HTTPException(status_code=502, detail=f"OSS upload failed for {raw_name or 'image'}")

            uploaded.append(
                {
                    "name": raw_name or object_name,
                    "object_name": object_name,
                    "url": f"{public_base}/{object_name}",
                }
            )
    except oss2.exceptions.OssError as exc:
        raise HTTPException(status_code=502, detail=f"OSS error: {exc}") from exc
    finally:
        for f in files:
            await f.close()

    if not uploaded:
        raise HTTPException(status_code=422, detail="No image files in upload")

    return {"uploaded": uploaded}


@app.post("/issuer/recognize")
async def recognize_issuer_from_images(payload: IssuerRecognitionRequest) -> dict[str, object]:
    notice_url = payload.notice_url.strip()
    if not notice_url:
        raise HTTPException(status_code=422, detail="notice_url must not be empty")

    image_urls = [str(u).strip() for u in payload.image_urls if str(u).strip()]
    if not image_urls:
        raise HTTPException(status_code=422, detail="image_urls must not be empty")

    try:
        recognition = await recognize_company_from_images(image_urls, timeout_seconds=payload.timeout_seconds)
        saved = await asyncio.to_thread(
            save_issuer_recognition_result,
            payload.source_url.strip(),
            notice_url,
            payload.issuer_name.strip(),
            recognition,
        )

        return {
            "status": "ok",
            "item_id": saved.get("item_id"),
            "item_patch": saved.get("item_patch") or {},
            "company_info": saved.get("company_info") or {},
            "image_urls": recognition.get("image_urls") or [],
        }
    except LLMClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save recognition result: {exc}") from exc

@app.post("/llm/chat", response_model=LLMChatResponse)
async def llm_chat(payload: LLMChatRequest) -> LLMChatResponse:
    try:
        content = await chat_completion(payload.prompt, timeout_seconds=payload.timeout_seconds)
        return LLMChatResponse(content=content)
    except LLMClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/results/mysql")
async def get_results_from_mysql(source_url: str | None = None, limit: int = 20) -> dict[str, dict]:
    try:
        data = await asyncio.to_thread(load_latest_results_from_mysql, source_url, limit)
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to query results from MySQL: {exc}") from exc
