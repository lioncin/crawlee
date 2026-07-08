# FastAPI URL Result API

A minimal API service to fetch one URL and return structured JSON.

## Setup

```bash
cd /home/linxing/git/crawlee/tools/fastapi-url-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8765 --reload
```

## Call

Return extracted content (default):

```bash
curl -X POST 'http://127.0.0.1:8765/fetch' \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.szse.cn/disclosure/notice/company/index.html"}'
```

For list pages like SZSE notices, response includes `items` with date/title/url.

Return text + full HTML:

```bash
curl -X POST 'http://127.0.0.1:8765/fetch' \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.szse.cn/disclosure/notice/company/index.html","include_html":true}'
```

Example response:

```json
{
  "url": "https://www.szse.cn/disclosure/notice/company/index.html",
  "final_url": "https://www.szse.cn/disclosure/notice/company/index.html",
  "status_code": 200,
  "title": "深交所主页",
  "html_length": 88203,
  "text": "2026-07-01 关于华润新能源控股有限公司股票上市交易的公告 https://www.szse.cn/disclosure/notice/company/t20260701_621407.html",
  "items": [
    {
      "date": "2026-07-01",
      "title": "关于华润新能源控股有限公司股票上市交易的公告",
      "url": "https://www.szse.cn/disclosure/notice/company/t20260701_621407.html"
    }
  ],
  "html": null
}
```
