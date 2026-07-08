from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Crawlee URL Result API", version="0.2.0")


class FetchRequest(BaseModel):
    url: HttpUrl
    timeout_seconds: float = 20.0
    include_html: bool = False


class NoticeItem(BaseModel):
    date: str
    title: str
    url: str


class FetchResponse(BaseModel):
    url: str
    final_url: str
    status_code: int
    title: str | None
    html_length: int
    text: str
    items: list[NoticeItem] = []
    html: str | None = None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_NOTICE_ITEM_RE = re.compile(
    r"var\s+curHref\s*=\s*'([^']+)'\s*;.*?var\s+curTitle\s*=\s*'([^']+)'\s*;.*?<span\s+class=\"time\">\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*</span>",
    re.IGNORECASE | re.DOTALL,
)


def extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    # Collapse whitespace to keep response concise and stable.
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/fetch", response_model=FetchResponse)
async def fetch_url(payload: FetchRequest) -> FetchResponse:
    timeout = httpx.Timeout(payload.timeout_seconds)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(str(payload.url))
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    html = response.text
    final_url = str(response.url)

    items = extract_notice_items(html, final_url)
    if items:
        text = "\n".join(f"{item.date} {item.title} {item.url}" for item in items)
    else:
        text = extract_text(html)

    return FetchResponse(
        url=str(payload.url),
        final_url=final_url,
        status_code=response.status_code,
        title=extract_title(html),
        html_length=len(html),
        text=text,
        items=items,
        html=html if payload.include_html else None,
    )
