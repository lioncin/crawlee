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
from mysql_store import load_ai_analysis_candidates, load_latest_ai_analysis_results, load_latest_results_from_mysql, replace_ai_analysis_results, save_issuer_recognition_result, store_fetch_result

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
    "https://www.cninfo.com.cn/new/index",
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
_CNINFO_QUICK_TABLE_RE = re.compile(
    r"<table[^>]*class=\"[^\"]*jc-table3[^\"]*\"[^>]*>.*?<thead>.*?<th>\s*代码\s*</th>.*?<th>\s*简称\s*</th>.*?<th>\s*公告标题\s*</th>.*?<th[^>]*>\s*日期\s*</th>.*?</thead>.*?<tbody>(.*?)</tbody>",
    re.IGNORECASE | re.DOTALL,
)
_CNINFO_ANNOUNCEMENT_DATE_RE = re.compile(r"[?&]announcementTime=([0-9]{4}-[0-9]{2}-[0-9]{2})")

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

def is_cninfo_index(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("cninfo.com.cn") and parsed.path.startswith("/new/index")


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

def parse_cninfo_date_from_detail_url(url: str) -> str:
    match = _CNINFO_ANNOUNCEMENT_DATE_RE.search(url)
    if not match:
        return ""
    return match.group(1)


def normalize_cninfo_mmdd(mmdd: str, default_year: int) -> str:
    value = mmdd.strip()
    if not value:
        return ""
    full = normalize_date_yyyymmdd(value)
    if full != value and re.match(r"^\d{4}-\d{2}-\d{2}$", full):
        return full

    m = re.match(r"^([0-1]?\d)-([0-3]?\d)$", value)
    if not m:
        return value
    month, day = m.groups()
    return f"{default_year:04d}-{int(month):02d}-{int(day):02d}"


def extract_cninfo_latest_notice_items(html: str, base_url: str) -> list[NoticeItem]:
    table_match = _CNINFO_QUICK_TABLE_RE.search(html)
    if not table_match:
        return []

    rows_html = table_match.group(1)
    rows = _HKEX_ROW_RE.findall(rows_html)
    current_year = datetime.now(timezone.utc).year

    items: list[NoticeItem] = []
    seen_links: set[str] = set()

    for row_html in rows:
        cells = _HKEX_CELL_RE.findall(row_html)
        if len(cells) < 4:
            continue

        stock_code = clean_html_fragment(cells[0])
        stock_name = clean_html_fragment(cells[1])
        title = clean_html_fragment(cells[2])
        date_text = clean_html_fragment(cells[3])

        detail_url = ""
        for raw_href in _HKEX_LINK_RE.findall(cells[2]):
            href = unescape(raw_href).strip()
            if "/new/disclosure/detail" not in href:
                continue
            detail_url = urljoin(base_url, href)
            break

        if not detail_url or detail_url in seen_links:
            continue
        seen_links.add(detail_url)

        date = parse_cninfo_date_from_detail_url(detail_url)
        if not date:
            date = normalize_cninfo_mmdd(date_text, current_year)

        display_name = stock_name or stock_code or "未知公司"
        if stock_code:
            normalized_title = f"{display_name}({stock_code}) {title}"
        else:
            normalized_title = f"{display_name} {title}"

        items.append(
            NoticeItem(
                date=date,
                title=normalized_title,
                url=detail_url,
                issuer_full_name=stock_name or None,
                board="巨潮资讯网",
                audit_status="最新公告",
                update_date=date or None,
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
    if not items and is_cninfo_index(final_url):
        items = extract_cninfo_latest_notice_items(html, final_url)

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


GRADE_ORDER = ["A", "B", "C", "D"]
GRADE_PRIORITY = {grade: idx for idx, grade in enumerate(GRADE_ORDER)}
_LINK_KEY_TOKENS = ("url", "link", "href", "website", "site", "官网", "链接", "网址")
_NOISY_KEY_TOKENS = ("html", "raw_text", "full_ocr_text", "ocr", "image", "screenshot")
_PHONE_KEY_TOKENS = ("phone", "tel", "mobile", "telephone", "联系电话", "电话", "手机")
_EMAIL_KEY_TOKENS = ("email", "mail", "邮箱")
_CONTACT_KEY_TOKENS = ("contact", "联系人", "person", "manager", "负责人", "对接")
_EMPLOYEE_COUNT_KEY_TOKENS = ("employee", "staff", "人员", "员工", "职工", "从业")
_INSURED_COUNT_KEY_TOKENS = ("insured", "insurance", "social_security", "社保", "参保")
_REVENUE_KEY_TOKENS = ("revenue", "income", "turnover", "营业收入", "营收", "销售额")


def _looks_like_link_value(value: str) -> bool:
    s = str(value or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("www.")


def _is_link_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return any(token in lowered for token in _LINK_KEY_TOKENS)


def _is_noisy_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return any(token in lowered for token in _NOISY_KEY_TOKENS)


def _strip_link_and_noise_fields(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, val in value.items():
            key_text = str(key)
            if _is_link_key(key_text) or _is_noisy_key(key_text):
                continue
            nested = _strip_link_and_noise_fields(val)
            if nested in (None, "", [], {}):
                continue
            cleaned[key_text] = nested
        return cleaned

    if isinstance(value, list):
        arr = []
        for item in value:
            nested = _strip_link_and_noise_fields(item)
            if nested in (None, "", [], {}):
                continue
            arr.append(nested)
        return arr

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if _looks_like_link_value(text):
            return ""
        if len(text) > 1000:
            return text[:1000]
        return text

    return value


def _iter_scalar_fields(value: object, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, val in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            rows.extend(_iter_scalar_fields(val, path))
        return rows

    if isinstance(value, list):
        for idx, item in enumerate(value):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            rows.extend(_iter_scalar_fields(item, path))
        return rows

    if value is None:
        return rows

    text = str(value).strip()
    if text:
        rows.append((prefix, text))
    return rows


def _find_first_value_by_tokens(value: object, key_tokens: tuple[str, ...], value_check=None) -> str:
    for path, text in _iter_scalar_fields(value):
        lower_path = path.lower()
        if not any(token in lower_path for token in key_tokens):
            continue
        if _looks_like_link_value(text):
            continue
        if value_check and not value_check(text):
            continue
        return text
    return ""


def _guess_phone(value: object) -> str:
    phone = _find_first_value_by_tokens(
        value,
        _PHONE_KEY_TOKENS,
        value_check=lambda x: bool(re.search(r"\d{6,}", x)),
    )
    return phone[:80] if phone else ""


def _guess_email(value: object) -> str:
    email = _find_first_value_by_tokens(
        value,
        _EMAIL_KEY_TOKENS,
        value_check=lambda x: "@" in x,
    )
    return email[:120] if email else ""


def _guess_contact_name(value: object) -> str:
    contact = _find_first_value_by_tokens(value, _CONTACT_KEY_TOKENS)
    return contact[:80] if contact else ""


def _guess_employee_count(value: object) -> str:
    employee_count = _find_first_value_by_tokens(
        value,
        _EMPLOYEE_COUNT_KEY_TOKENS,
        value_check=lambda x: bool(re.search(r"\d", x)),
    )
    return employee_count[:80] if employee_count else ""


def _guess_insured_count(value: object) -> str:
    insured_count = _find_first_value_by_tokens(
        value,
        _INSURED_COUNT_KEY_TOKENS,
        value_check=lambda x: bool(re.search(r"\d", x)),
    )
    return insured_count[:80] if insured_count else ""


def _guess_operating_revenue(value: object) -> str:
    operating_revenue = _find_first_value_by_tokens(
        value,
        _REVENUE_KEY_TOKENS,
        value_check=lambda x: bool(re.search(r"\d", x)),
    )
    return operating_revenue[:120] if operating_revenue else ""


def _pick_company_name(raw: dict[str, object], company_info: dict[str, object]) -> str:
    issuer_name = str(raw.get("issuer_full_name") or "").strip()
    if issuer_name:
        return issuer_name

    for key in ("issuer_full_name", "company_name", "name", "company", "企业名称", "公司名称"):
        val = company_info.get(key)
        if isinstance(val, str) and val.strip() and not _looks_like_link_value(val):
            return val.strip()

    title = str(raw.get("title") or "").strip()
    return title[:80] if title else ""


def _strip_code_fence(text: str) -> str:
    content = (text or "").strip()
    if not content.startswith("```"):
        return content

    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_array(content: str) -> list[dict[str, object]]:
    cleaned = _strip_code_fence(content)

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[(.*)\]", cleaned, flags=re.DOTALL)
    if not match:
        return []

    try:
        data = json.loads(f"[{match.group(1)}]")
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _normalize_grade(raw_grade: object) -> str:
    grade = str(raw_grade or "").strip().upper()
    return grade if grade in GRADE_PRIORITY else "D"


def _build_lead_analysis_prompt(rows: list[dict[str, object]]) -> str:
    rows_payload = [
        {
            "idx": row["idx"],
            "company_name": row["company_name"],
            "title": row["title"],
            "company_info": row["company_info"],
            "extra": row["extra"],
        }
        for row in rows
    ]

    return (
        "你是B2B软件销售线索分析专家。我们公司销售 minitab 软件。\n"
        "请根据输入公司数据，给每家公司评估采购 minitab 的成交可能性等级。\n"
        "业务背景：\n"
        "1) 大中型公司更可能采购，小型公司通常不会；\n"
        "2) 上市/拟上市公司对合规和流程规范要求更高，潜在需求更强；\n"
        "3) 可参考社保缴费人数、组织规模、行业属性、信息化成熟度、规范化诉求；\n"
        "4) 也请发挥你的分析能力补充其他合理判断维度。\n\n"
        "输出要求：\n"
        "1) 仅输出 JSON 数组，不要任何额外说明；\n"
        "2) 每个元素格式：{\"idx\":数字,\"grade\":\"A|B|C|D\",\"reason\":\"不超过80字\"};\n"
        "3) 必须覆盖输入中的每个 idx；\n"
        "4) A=需求最强，D=需求最弱。\n\n"
        f"输入数据：\n{json.dumps(rows_payload, ensure_ascii=False)}"
    )



@app.get("/analysis/lead-score")
async def get_analysis_lead_score() -> dict[str, object]:
    try:
        return await asyncio.to_thread(load_latest_ai_analysis_results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to query AI analysis results from MySQL: {exc}") from exc


@app.post("/analysis/lead-score")
async def analysis_lead_score() -> dict[str, object]:
    try:
        candidates = await asyncio.to_thread(load_ai_analysis_candidates, 2500)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load candidates from MySQL: {exc}") from exc

    if not candidates:
        empty_payload = {
            "summary": {
                "total": 0,
                "chunk_size": 0,
                "chunks": 0,
                "counts": {grade: 0 for grade in GRADE_ORDER},
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "groups": {grade: [] for grade in GRADE_ORDER},
        }
        try:
            await asyncio.to_thread(replace_ai_analysis_results, empty_payload["summary"], [])
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to replace AI analysis results in MySQL: {exc}") from exc
        return empty_payload

    prepared_rows: list[dict[str, object]] = []
    for idx, item in enumerate(candidates):
        company_info_obj = item.get("company_info") if isinstance(item.get("company_info"), dict) else {}
        extra_obj = item.get("extra") if isinstance(item.get("extra"), dict) else {}

        cleaned_company_info = _strip_link_and_noise_fields(company_info_obj)
        cleaned_extra = _strip_link_and_noise_fields(extra_obj)

        if not isinstance(cleaned_company_info, dict):
            cleaned_company_info = {}
        if not isinstance(cleaned_extra, dict):
            cleaned_extra = {}

        company_name = _pick_company_name(item, cleaned_company_info)
        title = str(item.get("title") or "").strip()

        contact_name = _guess_contact_name(cleaned_company_info) or _guess_contact_name(cleaned_extra)
        phone = _guess_phone(cleaned_company_info) or _guess_phone(cleaned_extra)
        email = _guess_email(cleaned_company_info) or _guess_email(cleaned_extra)
        employee_count = _guess_employee_count(cleaned_company_info) or _guess_employee_count(cleaned_extra)
        operating_revenue = _guess_operating_revenue(cleaned_company_info) or _guess_operating_revenue(cleaned_extra)
        insured_count = _guess_insured_count(cleaned_company_info) or _guess_insured_count(cleaned_extra)

        prepared_rows.append(
            {
                "idx": idx,
                "item_id": int(item.get("item_id") or 0),
                "company_name": company_name,
                "title": title,
                "item_date": str(item.get("item_date") or ""),
                "contact_name": contact_name,
                "phone": phone,
                "email": email,
                "employee_count": employee_count,
                "operating_revenue": operating_revenue,
                "insured_count": insured_count,
                "company_info": cleaned_company_info,
                "extra": cleaned_extra,
            }
        )

    chunk_size = 25
    chunk_count = (len(prepared_rows) + chunk_size - 1) // chunk_size

    analysis_by_idx: dict[int, dict[str, object]] = {}

    for chunk_start in range(0, len(prepared_rows), chunk_size):
        chunk = prepared_rows[chunk_start : chunk_start + chunk_size]
        prompt = _build_lead_analysis_prompt(chunk)

        try:
            llm_text = await chat_completion(prompt, timeout_seconds=120.0)
            parsed_rows = _parse_json_array(llm_text)
        except LLMClientError:
            parsed_rows = []

        for row in parsed_rows:
            idx_val = row.get("idx")
            if not isinstance(idx_val, int):
                continue
            if idx_val < 0 or idx_val >= len(prepared_rows):
                continue

            analysis_by_idx[idx_val] = {
                "grade": _normalize_grade(row.get("grade")),
                "reason": str(row.get("reason") or "").strip()[:120],
            }

        for item in chunk:
            idx_val = int(item["idx"])
            if idx_val in analysis_by_idx:
                continue
            analysis_by_idx[idx_val] = {
                "grade": "D",
                "reason": "当前信息不足，优先级较低",
            }

    final_rows: list[dict[str, object]] = []
    for row in prepared_rows:
        idx_val = int(row["idx"])
        scored = analysis_by_idx.get(idx_val) or {"grade": "D", "reason": "当前信息不足，优先级较低"}

        final_rows.append(
            {
                "grade": _normalize_grade(scored.get("grade")),
                "company_name": str(row.get("company_name") or ""),
                "title": str(row.get("title") or ""),
                "contact_name": str(row.get("contact_name") or ""),
                "phone": str(row.get("phone") or ""),
                "email": str(row.get("email") or ""),
                "employee_count": str(row.get("employee_count") or ""),
                "operating_revenue": str(row.get("operating_revenue") or ""),
                "insured_count": str(row.get("insured_count") or ""),
                "reason": str(scored.get("reason") or "").strip()[:120],
                "item_date": str(row.get("item_date") or ""),
            }
        )

    final_rows.sort(
        key=lambda x: (
            GRADE_PRIORITY.get(str(x.get("grade") or "D"), 3),
            str(x.get("item_date") or ""),
            str(x.get("company_name") or ""),
        ),
        reverse=False,
    )

    groups: dict[str, list[dict[str, object]]] = {grade: [] for grade in GRADE_ORDER}
    for row in final_rows:
        grade = _normalize_grade(row.get("grade"))
        groups[grade].append(row)

    counts = {grade: len(groups[grade]) for grade in GRADE_ORDER}

    response_payload = {
        "summary": {
            "total": len(final_rows),
            "chunk_size": chunk_size,
            "chunks": chunk_count,
            "counts": counts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "groups": groups,
    }

    try:
        await asyncio.to_thread(replace_ai_analysis_results, response_payload["summary"], final_rows)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to replace AI analysis results in MySQL: {exc}") from exc

    return response_payload
