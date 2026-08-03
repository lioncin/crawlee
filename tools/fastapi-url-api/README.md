# FastAPI URL Result API

A minimal API service to fetch one URL and return structured JSON.

## Setup

```bash
cd /home/linxing/git/crawlee/tools/fastapi-url-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## LLM Environment

Create local `.env` (do not commit):

```env
LLM_MODEL=gpt-5.5
LLM_PROVIDER=openai-custom
LLM_BASE_URL=https://api2.100zy.cn/v1
LLM_API_KEY=your_api_key_here
```

A template file is provided: `.env.example`.

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8765 --reload
```

## URL Fetch API

Manage the URLs included in a wildcard fetch (`{"url":"*"}`) through the API or the data-console UI:

```bash
# List configured URLs (10 records per page by default)
curl 'http://127.0.0.1:8765/crawl-targets?page=1&per_page=10'

# Add a URL
curl -X POST 'http://127.0.0.1:8765/crawl-targets' \
  -H 'content-type: application/json' \
  -d '{"name":"上交所 IPO","url":"https://example.com/list","is_active":true}'
```

Only enabled URLs are fetched. An empty list stays empty; URLs are never automatically restored after deletion or `TRUNCATE`.

Return extracted content (default):

```bash
curl -X POST 'http://127.0.0.1:8765/fetch' \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.szse.cn/disclosure/notice/company/index.html"}'
```

For list pages like SZSE/SSE/HKEX/CNINFO notices, response includes `items` with date/title/url.

Return text + full HTML:

```bash
curl -X POST 'http://127.0.0.1:8765/fetch' \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.szse.cn/disclosure/notice/company/index.html","include_html":true}'
```

## LLM API

```bash
curl -X POST 'http://127.0.0.1:8765/llm/chat' \
  -H 'content-type: application/json' \
  -d '{"prompt":"请总结今天的IPO公告重点"}'
```

Response:

```json
{
  "content": "..."
}
```
## MySQL Persistence

After each successful `/fetch`, the API will persist data into MySQL tables:

- `source_config`
- `crawl_record`
- `entity_item`
- `entity_kv`

Make sure `.env` has valid MySQL settings and tables are created from:

```bash
mysql -u root -p crawlee_data < /home/linxing/git/crawlee/sql/flexible_storage_schema.sql
```
