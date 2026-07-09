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
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _infer_source_type(source_url: str) -> str:
    u = source_url.lower()
    if "sse.com.cn" in u:
        return "sse"
    if "szse.cn" in u:
        return "szse"
    if "hkexnews.hk" in u:
        return "hkex"
    if "cninfo.com.cn" in u:
        return "cninfo"
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


def _loads_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _iter_pages(request_url: str, result_payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    if "status_code" in result_payload and "url" in result_payload:
        source_url = str(result_payload.get("url") or request_url)
        yield source_url, result_payload
        return

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


def _hydrate_page_items_from_entity(cur, source_url: str, page_payload: dict[str, Any]) -> None:
    items = page_payload.get("items")
    if not isinstance(items, list) or not items:
        return

    notice_urls = [
        str(item.get("url") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    if not notice_urls:
        return

    placeholders = ",".join(["%s"] * len(notice_urls))
    cur.execute(
        f"""
        SELECT x.notice_url, x.issuer_full_name, x.extra
        FROM (
          SELECT
            ei.url AS notice_url,
            ei.issuer_full_name,
            ei.extra,
            ROW_NUMBER() OVER (
              PARTITION BY ei.url
              ORDER BY cr.task_time DESC, cr.record_id DESC, ei.item_id DESC
            ) AS rn
          FROM entity_item ei
          JOIN crawl_record cr ON cr.record_id = ei.record_id
          JOIN source_config sc ON sc.source_id = cr.source_id
          WHERE sc.source_url = %s
            AND ei.url IN ({placeholders})
        ) x
        WHERE x.rn = 1
        """,
        (source_url, *notice_urls),
    )
    rows = cur.fetchall() or []
    by_notice_url = {str(row.get("notice_url") or ""): row for row in rows}

    for item in items:
        if not isinstance(item, dict):
            continue

        notice_url = str(item.get("url") or "").strip()
        if not notice_url:
            continue

        row = by_notice_url.get(notice_url)
        if not row:
            continue

        issuer_name = (row.get("issuer_full_name") or "").strip()
        if issuer_name:
            item["issuer_full_name"] = issuer_name

        row_extra = _loads_json(row.get("extra"))
        if not isinstance(row_extra, dict):
            continue

        current_extra = item.get("extra")
        merged_extra = current_extra if isinstance(current_extra, dict) else {}
        merged_extra.update(row_extra)
        item["extra"] = merged_extra

        ai_recognition = row_extra.get("ai_recognition")
        if isinstance(ai_recognition, dict):
            company_info = ai_recognition.get("company_info")
            if isinstance(company_info, dict):
                item["company_info"] = company_info

        if not isinstance(item.get("company_info"), dict):
            ai_company_info = row_extra.get("ai_company_info")
            if isinstance(ai_company_info, dict):
                item["company_info"] = ai_company_info


def load_latest_results_from_mysql(source_url: str | None = None, limit: int = 20) -> dict[str, Any]:
    cfg = _mysql_config()
    safe_limit = max(1, min(int(limit), 100))

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            if source_url:
                cur.execute(
                    """
                    SELECT sc.source_url, cr.raw_payload
                    FROM crawl_record cr
                    JOIN source_config sc ON sc.source_id = cr.source_id
                    WHERE sc.source_url = %s
                    ORDER BY cr.task_time DESC, cr.record_id DESC
                    LIMIT 1
                    """,
                    (source_url,),
                )
                row = cur.fetchone()
                if not row:
                    return {}
                payload = _loads_json(row.get("raw_payload")) or {}
                src = str(row.get("source_url"))
                if isinstance(payload, dict):
                    _hydrate_page_items_from_entity(cur, src, payload)
                return {src: payload}

            cur.execute(
                """
                SELECT x.source_url, x.raw_payload
                FROM (
                  SELECT
                    sc.source_url,
                    cr.raw_payload,
                    cr.task_time,
                    cr.record_id,
                    ROW_NUMBER() OVER (
                      PARTITION BY cr.source_id
                      ORDER BY cr.task_time DESC, cr.record_id DESC
                    ) AS rn
                  FROM crawl_record cr
                  JOIN source_config sc ON sc.source_id = cr.source_id
                ) x
                WHERE x.rn = 1
                ORDER BY x.task_time DESC, x.record_id DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            rows = cur.fetchall() or []

            result: dict[str, Any] = {}
            for row in rows:
                src = str(row.get("source_url"))
                payload = _loads_json(row.get("raw_payload"))
                if payload is None:
                    continue
                if isinstance(payload, dict):
                    _hydrate_page_items_from_entity(cur, src, payload)
                result[src] = payload

            return result


def save_issuer_recognition_result(
    source_url: str,
    notice_url: str,
    issuer_name: str | None,
    recognition_payload: dict[str, Any],
) -> dict[str, Any]:
    cfg = _mysql_config()
    safe_source_url = (source_url or "").strip()
    safe_notice_url = (notice_url or "").strip()
    safe_issuer_name = (issuer_name or "").strip()

    if not safe_notice_url:
        raise ValueError("notice_url must not be empty")

    parsed = recognition_payload.get("parsed") if isinstance(recognition_payload, dict) else None
    parsed = parsed if isinstance(parsed, dict) else {}
    company_info = parsed.get("company_info") if isinstance(parsed.get("company_info"), dict) else {}

    # Keep a display patch aligned with UI columns.
    patch: dict[str, Any] = {}
    issuer_from_ai = (
        company_info.get("issuer_full_name")
        or company_info.get("company_name")
        or company_info.get("name")
    )
    if issuer_from_ai or safe_issuer_name:
        patch["issuer_full_name"] = str(issuer_from_ai or safe_issuer_name).strip() or None

    for key in (
        "board",
        "audit_status",
        "province",
        "industry",
        "sponsor",
        "law_firm",
        "accounting_firm",
        "update_date",
        "accept_date",
    ):
        val = company_info.get(key)
        if val is not None:
            patch[key] = str(val).strip() or None

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            params: list[Any] = [safe_notice_url]
            where_sql = "ei.url = %s"

            if safe_source_url:
                where_sql += " AND sc.source_url = %s"
                params.append(safe_source_url)

            if safe_issuer_name:
                where_sql += " AND (ei.issuer_full_name = %s OR ei.issuer_full_name IS NULL OR ei.issuer_full_name = '')"
                params.append(safe_issuer_name)

            cur.execute(
                f"""
                SELECT ei.item_id, ei.extra
                FROM entity_item ei
                JOIN crawl_record cr ON cr.record_id = ei.record_id
                JOIN source_config sc ON sc.source_id = cr.source_id
                WHERE {where_sql}
                ORDER BY cr.task_time DESC, cr.record_id DESC, ei.item_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Target item not found in MySQL")

            item_id = int(row["item_id"])
            old_extra = _loads_json(row.get("extra"))
            merged_extra = old_extra if isinstance(old_extra, dict) else {}
            merged_extra.update(
                {
                    "ai_recognition": parsed if parsed else None,
                    "ai_image_urls": recognition_payload.get("image_urls") or [],
                    "ai_model": recognition_payload.get("model"),
                    "ai_raw_text": recognition_payload.get("raw_text"),
                    "ai_updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )

            cur.execute("UPDATE entity_item SET extra = %s WHERE item_id = %s", (_to_json(merged_extra), item_id))

            if patch.get("issuer_full_name"):
                cur.execute(
                    "UPDATE entity_item SET issuer_full_name = %s WHERE item_id = %s",
                    (patch.get("issuer_full_name"), item_id),
                )

            cur.execute("DELETE FROM entity_kv WHERE item_id = %s AND field_key LIKE 'ai_%%'", (item_id,))

            ai_kv_rows = [
                ("ai_company_info", company_info if isinstance(company_info, dict) else None),
                ("ai_evidence", parsed.get("evidence") if isinstance(parsed, dict) else None),
                ("ai_full_ocr_text", parsed.get("full_ocr_text") if isinstance(parsed, dict) else None),
                ("ai_uncertain_items", parsed.get("uncertain_items") if isinstance(parsed, dict) else None),
                ("ai_image_urls", recognition_payload.get("image_urls") or []),
                ("ai_model", recognition_payload.get("model")),
            ]

            for field_key, value in ai_kv_rows:
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
                    (item_id, field_key, field_value, field_type),
                )

        conn.commit()

    return {
        "item_id": item_id,
        "item_patch": patch,
        "company_info": company_info,
    }


def load_ai_analysis_candidates(limit: int = 2000) -> list[dict[str, Any]]:
    cfg = _mysql_config()
    safe_limit = max(1, min(int(limit), 5000))

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  ei.item_id,
                  ei.title,
                  ei.issuer_full_name,
                  ei.item_date,
                  ei.extra,
                  sc.source_url,
                  cr.task_time
                FROM entity_item ei
                JOIN crawl_record cr ON cr.record_id = ei.record_id
                JOIN source_config sc ON sc.source_id = cr.source_id
                ORDER BY COALESCE(ei.item_date, DATE(cr.task_time)) DESC, ei.updated_at DESC, ei.item_id DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            rows = cur.fetchall() or []

    result: list[dict[str, Any]] = []
    for row in rows:
        extra = _loads_json(row.get("extra"))
        extra_obj = extra if isinstance(extra, dict) else {}

        company_info: dict[str, Any] = {}
        ai_recognition = extra_obj.get("ai_recognition")
        if isinstance(ai_recognition, dict) and isinstance(ai_recognition.get("company_info"), dict):
            company_info = ai_recognition.get("company_info") or {}
        elif isinstance(extra_obj.get("ai_company_info"), dict):
            company_info = extra_obj.get("ai_company_info") or {}
        elif isinstance(extra_obj.get("company_info"), dict):
            company_info = extra_obj.get("company_info") or {}

        item_date = row.get("item_date")
        result.append(
            {
                "item_id": int(row.get("item_id") or 0),
                "source_url": str(row.get("source_url") or ""),
                "title": str(row.get("title") or ""),
                "issuer_full_name": str(row.get("issuer_full_name") or "").strip(),
                "item_date": item_date.isoformat() if item_date else "",
                "company_info": company_info,
                "extra": extra_obj,
            }
        )

    return result
