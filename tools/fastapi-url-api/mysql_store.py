from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pymysql


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _mysql_config() -> dict[str, Any]:
    env_path = Path(__file__).with_name(".env")
    _load_dotenv(env_path)

    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "crawlee_data"),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def _infer_source_type(source_url: str) -> str:
    u = source_url.lower()
    if "sse.com.cn" in u:
        return "sse"
    if "szse.cn" in u:
        return "szse"
    return "custom"


def _parse_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _iter_pages(request_url: str, result_payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    # Single page shape
    if "status_code" in result_payload and "url" in result_payload:
        source_url = str(result_payload.get("url") or request_url)
        yield source_url, result_payload
        return

    # Multi-page shape: {source_url: {...}}
    for source_url, page in result_payload.items():
        if isinstance(page, dict):
            yield str(source_url), page


def _upsert_source(cur, source_url: str) -> int:
    cur.execute(
        """
        INSERT INTO source_config (source_url, source_type, parser_version, is_active)
        VALUES (%s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE
          source_type = VALUES(source_type),
          parser_version = VALUES(parser_version),
          is_active = 1,
          source_id = LAST_INSERT_ID(source_id)
        """,
        (source_url, _infer_source_type(source_url), "v1"),
    )
    return int(cur.lastrowid)


def _insert_crawl_record(cur, source_id: int, page_data: dict[str, Any]) -> int:
    normalized = {
        "url": page_data.get("url"),
        "final_url": page_data.get("final_url"),
        "status_code": page_data.get("status_code"),
        "title": page_data.get("title"),
        "html_length": page_data.get("html_length"),
        "items_count": len(page_data.get("items") or []),
    }

    cur.execute(
        """
        INSERT INTO crawl_record (source_id, status_code, raw_payload, normalized_payload, error_message)
        VALUES (%s, %s, %s, %s, NULL)
        """,
        (
            source_id,
            page_data.get("status_code"),
            _to_json(page_data),
            _to_json(normalized),
        ),
    )
    return int(cur.lastrowid)


def _upsert_item(cur, record_id: int, source_url: str, item: dict[str, Any]) -> int:
    title = str(item.get("title") or "").strip()
    notice_url = str(item.get("url") or "").strip()
    item_date = _parse_date(item.get("date"))

    digest_raw = f"{source_url}|{notice_url}|{item_date or ''}|{title}"
    biz_key = hashlib.sha1(digest_raw.encode("utf-8")).hexdigest()

    common_fields = {
        "date",
        "title",
        "url",
        "issuer_full_name",
        "board",
        "audit_status",
        "province",
        "industry",
        "sponsor",
        "law_firm",
        "accounting_firm",
        "update_date",
        "accept_date",
    }
    extra = {k: v for k, v in item.items() if k not in common_fields}

    cur.execute(
        """
        INSERT INTO entity_item (
          record_id, biz_key, title, url, issuer_full_name, item_date, extra
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          record_id = VALUES(record_id),
          title = VALUES(title),
          url = VALUES(url),
          issuer_full_name = VALUES(issuer_full_name),
          item_date = VALUES(item_date),
          extra = VALUES(extra),
          item_id = LAST_INSERT_ID(item_id),
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            record_id,
            biz_key,
            title or None,
            notice_url or None,
            (item.get("issuer_full_name") or None),
            item_date,
            _to_json(extra) if extra else None,
        ),
    )
    return int(cur.lastrowid)


def _save_item_kv(cur, item_id: int, item: dict[str, Any]) -> None:
    common_fields = {
        "date",
        "title",
        "url",
        "issuer_full_name",
        "board",
        "audit_status",
        "province",
        "industry",
        "sponsor",
        "law_firm",
        "accounting_firm",
        "update_date",
        "accept_date",
    }

    cur.execute("DELETE FROM entity_kv WHERE item_id = %s", (item_id,))

    for key, value in item.items():
        if key in common_fields:
            continue

        if value is None:
            field_type = "null"
            field_value = None
        elif isinstance(value, bool):
            field_type = "bool"
            field_value = "1" if value else "0"
        elif isinstance(value, (int, float)):
            field_type = "number"
            field_value = str(value)
        elif isinstance(value, str):
            field_type = "string"
            field_value = value
        else:
            field_type = "json"
            field_value = _to_json(value)

        cur.execute(
            """
            INSERT INTO entity_kv (item_id, field_key, field_value, field_type)
            VALUES (%s, %s, %s, %s)
            """,
            (item_id, key, field_value, field_type),
        )


def store_fetch_result(request_url: str, result_payload: dict[str, Any]) -> None:
    cfg = _mysql_config()

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            for source_url, page_data in _iter_pages(request_url, result_payload):
                source_id = _upsert_source(cur, source_url)
                record_id = _insert_crawl_record(cur, source_id, page_data)

                for item in page_data.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    item_id = _upsert_item(cur, record_id, source_url, item)
                    _save_item_kv(cur, item_id, item)

        conn.commit()
