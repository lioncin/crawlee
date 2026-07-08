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