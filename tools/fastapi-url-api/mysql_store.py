from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pymysql

logger = logging.getLogger(__name__)

EDIT_STATUS_UNEDITED = "未编辑"
EDIT_STATUS_EDITED = "已编辑"


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


def _pick_first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def _to_bool01(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "有", "覆盖", "有效"}:
        return 1
    if text in {"0", "false", "no", "n", "否", "无", "不覆盖", "无效"}:
        return 0
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _pick_company_info_value(company_info: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = company_info.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_company_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _get_item_company_name(item: dict[str, Any]) -> str:
    company_name = _normalize_company_name(item.get("issuer_full_name"))
    if company_name:
        return company_name

    company_name = _normalize_company_name(item.get("llm_issuer_full_name"))
    if company_name:
        return company_name

    company_info = item.get("company_info")
    if isinstance(company_info, dict):
        for key in ("issuer_full_name", "company_name", "name"):
            company_name = _normalize_company_name(company_info.get(key))
            if company_name:
                return company_name

    return ""


def _load_existing_company_names(cur, company_names: set[str]) -> set[str]:
    if not company_names:
        return set()

    placeholders = ",".join(["%s"] * len(company_names))
    existing_company_names: set[str] = set()

    cur.execute(
        f"""
        SELECT DISTINCT issuer_full_name
        FROM entity_item
        WHERE issuer_full_name IN ({placeholders})
        """,
        tuple(company_names),
    )
    existing_company_names.update(
        _normalize_company_name(row.get("issuer_full_name"))
        for row in cur.fetchall() or []
        if _normalize_company_name(row.get("issuer_full_name"))
    )

    for table_name in ("company_profile", "company_registry_profile"):
        try:
            cur.execute(
                f"""
                SELECT DISTINCT company_name
                FROM {table_name}
                WHERE company_name IN ({placeholders})
                """,
                tuple(company_names),
            )
        except pymysql.err.ProgrammingError as exc:
            if exc.args and exc.args[0] == 1146:
                continue
            raise

        existing_company_names.update(
            _normalize_company_name(row.get("company_name"))
            for row in cur.fetchall() or []
            if _normalize_company_name(row.get("company_name"))
        )

    return existing_company_names


def _filter_new_company_items(cur, items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []

    valid_items = [item for item in items if isinstance(item, dict)]
    candidate_company_names = {
        company_name
        for item in valid_items
        if (company_name := _get_item_company_name(item))
    }
    existing_company_names = _load_existing_company_names(cur, candidate_company_names)

    filtered: list[dict[str, Any]] = []
    seen_company_names: set[str] = set()

    for item in valid_items:
        company_name = _get_item_company_name(item)
        if company_name:
            if company_name in existing_company_names or company_name in seen_company_names:
                logger.info("Skip existing company item: %s", company_name)
                continue
            seen_company_names.add(company_name)

        filtered.append(item)

    return filtered


def _with_default_edit_status(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("edit_status"):
        return item

    normalized = dict(item)
    normalized["edit_status"] = EDIT_STATUS_UNEDITED
    return normalized


def _normalize_company_info_fields(company_info: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(company_info, dict):
        return {}

    normalized = dict(company_info)

    normalized["issuer_full_name"] = _pick_company_info_value(
        normalized,
        "issuer_full_name",
        "company_name",
        "name",
    )
    normalized["company_name"] = _pick_company_info_value(
        normalized,
        "company_name",
        "issuer_full_name",
        "name",
    )

    normalized["contact_name"] = _pick_company_info_value(normalized, "contact_name", "contact", "contact_person")
    normalized["phone"] = _pick_company_info_value(normalized, "phone", "telephone", "mobile")
    normalized["email"] = _pick_company_info_value(normalized, "email", "mail")

    normalized["employee_count"] = _pick_company_info_value(
        normalized,
        "employee_count",
        "employees_text",
        "employees",
        "staff_count",
        "employee_num",
        "staff_num",
    )
    normalized["operating_revenue"] = _pick_company_info_value(
        normalized,
        "operating_revenue",
        "revenue_text",
        "revenue",
        "annual_revenue",
        "business_revenue",
    )
    normalized["insured_count"] = _pick_company_info_value(
        normalized,
        "insured_count",
        "insured_persons",
        "branch_insured_persons",
        "insured_num",
        "social_security_count",
    )

    certificates = normalized.get("certificates")
    if certificates is None:
        certificates = normalized.get("company_certificate")
    if isinstance(certificates, dict):
        certificates = [certificates]
    if not isinstance(certificates, list):
        certificates = []
    normalized["certificates"] = [x for x in certificates if isinstance(x, dict)]

    parsed_employee = _to_int(normalized.get("employee_count"))
    if parsed_employee is not None:
        normalized["employee_count"] = str(parsed_employee)

    parsed_insured = _to_int(normalized.get("insured_count"))
    if parsed_insured is not None:
        normalized["insured_count"] = str(parsed_insured)

    return normalized


def _upsert_company_profile(cur, item_id: int, source_url: str, notice_url: str, parsed: dict[str, Any], company_info: dict[str, Any]) -> int:
    basic_info = parsed.get("basic_info") if isinstance(parsed.get("basic_info"), dict) else {}
    contact_info = parsed.get("contact_info") if isinstance(parsed.get("contact_info"), dict) else {}
    business_data = parsed.get("business_data") if isinstance(parsed.get("business_data"), dict) else {}
    scores = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}

    company_name = _pick_first(company_info, "company_name", "issuer_full_name") or _pick_first(basic_info, "company_name")
    if not company_name:
        company_name = _pick_first(parsed, "company_name", "issuer_full_name") or ""

    values = (
        item_id,
        str(company_name or ""),
        _pick_first(parsed, "status", "registration_status"),
        _pick_first(parsed, "brand_logo"),
        _pick_first(parsed.get("stock_info") if isinstance(parsed.get("stock_info"), dict) else {}, "code", "stock_code"),
        _pick_first(parsed.get("stock_info") if isinstance(parsed.get("stock_info"), dict) else {}, "market", "stock_market"),
        _pick_first(basic_info, "unified_social_credit_code"),
        _pick_first(basic_info, "legal_representative"),
        _pick_first(basic_info, "registered_capital"),
        _parse_date(_pick_first(basic_info, "establishment_date")),
        _pick_first(contact_info, "phone"),
        _pick_first(contact_info, "email"),
        _pick_first(contact_info, "website"),
        _pick_first(contact_info, "address", "registered_address"),
        _pick_first(business_data, "industry") or _pick_first(basic_info, "industry"),
        _pick_first(business_data, "scale"),
        _pick_first(business_data, "employees", "employee_count"),
        _pick_first(business_data, "revenue", "operating_revenue"),
        _pick_first(scores, "enterprise_score"),
        _pick_first(scores, "tech_innovation_score"),
        _to_json(parsed.get("tags") if isinstance(parsed.get("tags"), list) else []),
        _to_json(scores),
        _to_json(basic_info),
        _to_json(contact_info),
        _to_json(business_data),
        _pick_first(parsed, "ai_summary"),
        _to_json({"source_url": source_url, "notice_url": notice_url, "parsed": parsed}),
    )

    try:
        cur.execute(
            """
            INSERT INTO company_profile (
              source_item_id, company_name, status, brand_logo,
              stock_code, stock_market,
              unified_social_credit_code, legal_representative, registered_capital, establishment_date,
              phone, email, website, address,
              industry, scale, employees_text, revenue_text,
              enterprise_score_text, tech_innovation_score_text,
              tags_json, scores_json, basic_info_json, contact_info_json, business_data_json,
              ai_summary, raw_payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              source_item_id = VALUES(source_item_id),
              status = VALUES(status),
              brand_logo = VALUES(brand_logo),
              stock_market = VALUES(stock_market),
              legal_representative = VALUES(legal_representative),
              registered_capital = VALUES(registered_capital),
              establishment_date = VALUES(establishment_date),
              phone = VALUES(phone),
              email = VALUES(email),
              website = VALUES(website),
              address = VALUES(address),
              industry = VALUES(industry),
              scale = VALUES(scale),
              employees_text = VALUES(employees_text),
              revenue_text = VALUES(revenue_text),
              enterprise_score_text = VALUES(enterprise_score_text),
              tech_innovation_score_text = VALUES(tech_innovation_score_text),
              tags_json = VALUES(tags_json),
              scores_json = VALUES(scores_json),
              basic_info_json = VALUES(basic_info_json),
              contact_info_json = VALUES(contact_info_json),
              business_data_json = VALUES(business_data_json),
              ai_summary = VALUES(ai_summary),
              raw_payload = VALUES(raw_payload),
              updated_at = CURRENT_TIMESTAMP,
              id = LAST_INSERT_ID(id)
            """,
            values,
        )
    except pymysql.err.OperationalError as exc:
        if getattr(exc, "args", ()) and len(exc.args) >= 2 and exc.args[0] == 1054 and "source_item_id" in str(exc.args[1]):
            cur.execute(
                """
                INSERT INTO company_profile (
                  company_name, status, brand_logo,
                  stock_code, stock_market,
                  unified_social_credit_code, legal_representative, registered_capital, establishment_date,
                  phone, email, website, address,
                  industry, scale, employees_text, revenue_text,
                  enterprise_score_text, tech_innovation_score_text,
                  tags_json, scores_json, basic_info_json, contact_info_json, business_data_json,
                  ai_summary, raw_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  status = VALUES(status),
                  brand_logo = VALUES(brand_logo),
                  stock_market = VALUES(stock_market),
                  legal_representative = VALUES(legal_representative),
                  registered_capital = VALUES(registered_capital),
                  establishment_date = VALUES(establishment_date),
                  phone = VALUES(phone),
                  email = VALUES(email),
                  website = VALUES(website),
                  address = VALUES(address),
                  industry = VALUES(industry),
                  scale = VALUES(scale),
                  employees_text = VALUES(employees_text),
                  revenue_text = VALUES(revenue_text),
                  enterprise_score_text = VALUES(enterprise_score_text),
                  tech_innovation_score_text = VALUES(tech_innovation_score_text),
                  tags_json = VALUES(tags_json),
                  scores_json = VALUES(scores_json),
                  basic_info_json = VALUES(basic_info_json),
                  contact_info_json = VALUES(contact_info_json),
                  business_data_json = VALUES(business_data_json),
                  ai_summary = VALUES(ai_summary),
                  raw_payload = VALUES(raw_payload),
                  updated_at = CURRENT_TIMESTAMP,
                  id = LAST_INSERT_ID(id)
                """,
                values[1:],
            )
        else:
            raise
    return int(cur.lastrowid)


def _upsert_company_registry_profile(cur, company_profile_id: int | None, item_id: int, parsed: dict[str, Any]) -> int | None:
    basic_info = parsed.get("basic_info") if isinstance(parsed.get("basic_info"), dict) else {}
    if not basic_info:
        return None

    uscc = _pick_first(basic_info, "unified_social_credit_code")
    company_name = _pick_first(basic_info, "company_name")
    if not uscc or not company_name:
        return None

    cur.execute(
        """
        INSERT INTO company_registry_profile (
          source_item_id, unified_social_credit_code, company_name,
          registration_status, establishment_date, legal_representative,
          registered_capital, paid_in_capital,
          organization_code, registration_number, taxpayer_id,
          company_type, business_term, taxpayer_qualification,
          insured_persons, branch_insured_persons, approval_date,
          region, registration_authority, import_export_code, industry,
          english_name, registered_address,
          former_names, basic_info_json, business_scope, raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          source_item_id = VALUES(source_item_id),
          company_name = VALUES(company_name),
          registration_status = VALUES(registration_status),
          establishment_date = VALUES(establishment_date),
          legal_representative = VALUES(legal_representative),
          registered_capital = VALUES(registered_capital),
          paid_in_capital = VALUES(paid_in_capital),
          organization_code = VALUES(organization_code),
          registration_number = VALUES(registration_number),
          taxpayer_id = VALUES(taxpayer_id),
          company_type = VALUES(company_type),
          business_term = VALUES(business_term),
          taxpayer_qualification = VALUES(taxpayer_qualification),
          insured_persons = VALUES(insured_persons),
          branch_insured_persons = VALUES(branch_insured_persons),
          approval_date = VALUES(approval_date),
          region = VALUES(region),
          registration_authority = VALUES(registration_authority),
          import_export_code = VALUES(import_export_code),
          industry = VALUES(industry),
          english_name = VALUES(english_name),
          registered_address = VALUES(registered_address),
          former_names = VALUES(former_names),
          basic_info_json = VALUES(basic_info_json),
          business_scope = VALUES(business_scope),
          raw_payload = VALUES(raw_payload),
          updated_at = CURRENT_TIMESTAMP,
          id = LAST_INSERT_ID(id)
        """,
        (
            item_id,
            str(uscc),
            str(company_name),
            _pick_first(basic_info, "registration_status", "status"),
            _parse_date(_pick_first(basic_info, "establishment_date")),
            _pick_first(basic_info, "legal_representative"),
            _pick_first(basic_info, "registered_capital"),
            _pick_first(basic_info, "paid_in_capital"),
            _pick_first(basic_info, "organization_code"),
            _pick_first(basic_info, "registration_number"),
            _pick_first(basic_info, "taxpayer_id"),
            _pick_first(basic_info, "company_type"),
            _pick_first(basic_info, "business_term"),
            _pick_first(basic_info, "taxpayer_qualification"),
            _pick_first(basic_info, "insured_persons"),
            _pick_first(basic_info, "branch_insured_persons"),
            _parse_date(_pick_first(basic_info, "approval_date")),
            _pick_first(basic_info, "region"),
            _pick_first(basic_info, "registration_authority"),
            _pick_first(basic_info, "import_export_code"),
            _pick_first(basic_info, "industry"),
            _pick_first(basic_info, "english_name"),
            _pick_first(basic_info, "registered_address"),
            _to_json(_pick_first(basic_info, "former_names") if isinstance(_pick_first(basic_info, "former_names"), list) else []),
            _to_json(basic_info),
            _pick_first(parsed, "business_scope"),
            _to_json(parsed),
        ),
    )
    return int(cur.lastrowid)


def _upsert_company_certificates(cur, company_profile_id: int | None, item_id: int, parsed: dict[str, Any], company_info: dict[str, Any]) -> int:
    cert_rows: list[dict[str, Any]] = []
    cert_value = _pick_first(company_info, "certificates", "certificate_list")
    if isinstance(cert_value, list):
        cert_rows.extend([x for x in cert_value if isinstance(x, dict)])
    elif isinstance(cert_value, dict):
        cert_rows.append(cert_value)

    if isinstance(parsed.get("certificate"), dict):
        cert_rows.append(parsed.get("certificate"))
    if isinstance(parsed.get("certificates"), list):
        cert_rows.extend([x for x in parsed.get("certificates") if isinstance(x, dict)])

    affected = 0
    for cert in cert_rows:
        cert_no = _pick_first(cert, "certificate_no", "证书编号")
        if not cert_no:
            continue

        cert_values = (
            company_profile_id,
            item_id,
            str(cert_no),
            _pick_first(cert, "certificate_status", "证书状态"),
            _parse_date(_pick_first(cert, "issue_date", "颁证日期")),
            _parse_date(_pick_first(cert, "expiry_date", "证书到期日期")),
            _parse_date(_pick_first(cert, "first_issue_date", "初次获证日期")),
            _parse_date(_pick_first(cert, "report_date", "信息上报日期")),
            _to_int(_pick_first(cert, "supervision_count", "监督次数")),
            _to_int(_pick_first(cert, "recertification_count", "再认证次数")),
            _pick_first(cert, "certification_project", "认证项目"),
            _pick_first(cert, "accreditation_mark", "证书使用的认可标识"),
            _pick_first(cert, "certification_scope", "认证范围/认证覆盖的业务范围"),
            _pick_first(cert, "certification_basis", "认证依据"),
            _to_bool01(_pick_first(cert, "covers_multiple_sites", "是否覆盖多场所")),
            _to_bool01(_pick_first(cert, "is_sub_certificate", "是否是子证书")),
            _pick_first(cert, "parent_certificate_no", "主认证证书号"),
            _to_json(cert),
        )

        try:
            cur.execute(
                """
                INSERT INTO company_certificate (
                  company_id, source_item_id,
                  certificate_no, certificate_status,
                  issue_date, expiry_date, first_issue_date, report_date,
                  supervision_count, recertification_count,
                  certification_project, accreditation_mark,
                  certification_scope, certification_basis,
                  covers_multiple_sites, is_sub_certificate, parent_certificate_no,
                  raw_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  company_id = VALUES(company_id),
                  source_item_id = VALUES(source_item_id),
                  certificate_status = VALUES(certificate_status),
                  issue_date = VALUES(issue_date),
                  expiry_date = VALUES(expiry_date),
                  first_issue_date = VALUES(first_issue_date),
                  report_date = VALUES(report_date),
                  supervision_count = VALUES(supervision_count),
                  recertification_count = VALUES(recertification_count),
                  certification_project = VALUES(certification_project),
                  accreditation_mark = VALUES(accreditation_mark),
                  certification_scope = VALUES(certification_scope),
                  certification_basis = VALUES(certification_basis),
                  covers_multiple_sites = VALUES(covers_multiple_sites),
                  is_sub_certificate = VALUES(is_sub_certificate),
                  parent_certificate_no = VALUES(parent_certificate_no),
                  raw_payload = VALUES(raw_payload),
                  updated_at = CURRENT_TIMESTAMP
                """,
                cert_values,
            )
        except pymysql.err.OperationalError as exc:
            if getattr(exc, "args", ()) and len(exc.args) >= 2 and exc.args[0] == 1054 and "source_item_id" in str(exc.args[1]):
                logger.warning("[_upsert_company_certificates] source_item_id missing; fallback SQL branch used")
                cur.execute(
                    """
                    INSERT INTO company_certificate (
                      company_id,
                      certificate_no, certificate_status,
                      issue_date, expiry_date, first_issue_date, report_date,
                      supervision_count, recertification_count,
                      certification_project, accreditation_mark,
                      certification_scope, certification_basis,
                      covers_multiple_sites, is_sub_certificate, parent_certificate_no,
                      raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      company_id = VALUES(company_id),
                      certificate_status = VALUES(certificate_status),
                      issue_date = VALUES(issue_date),
                      expiry_date = VALUES(expiry_date),
                      first_issue_date = VALUES(first_issue_date),
                      report_date = VALUES(report_date),
                      supervision_count = VALUES(supervision_count),
                      recertification_count = VALUES(recertification_count),
                      certification_project = VALUES(certification_project),
                      accreditation_mark = VALUES(accreditation_mark),
                      certification_scope = VALUES(certification_scope),
                      certification_basis = VALUES(certification_basis),
                      covers_multiple_sites = VALUES(covers_multiple_sites),
                      is_sub_certificate = VALUES(is_sub_certificate),
                      parent_certificate_no = VALUES(parent_certificate_no),
                      raw_payload = VALUES(raw_payload),
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        cert_values[0],
                        cert_values[2],
                        cert_values[3],
                        cert_values[4],
                        cert_values[5],
                        cert_values[6],
                        cert_values[7],
                        cert_values[8],
                        cert_values[9],
                        cert_values[10],
                        cert_values[11],
                        cert_values[12],
                        cert_values[13],
                        cert_values[14],
                        cert_values[15],
                        cert_values[16],
                        cert_values[17],
                    ),
                )
            else:
                raise
        affected += 1

    return affected



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
    audit_status = str(item.get("audit_status") or "").strip()
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
          record_id, biz_key, title, url, issuer_full_name, audit_status, item_date, extra
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          record_id = VALUES(record_id),
          title = VALUES(title),
          url = VALUES(url),
          issuer_full_name = VALUES(issuer_full_name),
          audit_status = VALUES(audit_status),
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
            audit_status or None,
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
                if not isinstance(page_data, dict):
                    continue

                filtered_items = [
                    _with_default_edit_status(item)
                    for item in _filter_new_company_items(cur, page_data.get("items"))
                ]
                page_data = dict(page_data)
                page_data["items"] = filtered_items

                source_id = _upsert_source(cur, source_url)
                record_id = _insert_crawl_record(cur, source_id, page_data)

                for item in filtered_items:
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
        SELECT x.notice_url, x.issuer_full_name, x.audit_status, x.extra
        FROM (
          SELECT
            ei.url AS notice_url,
            ei.issuer_full_name,
            ei.audit_status,
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

        audit_status = (row.get("audit_status") or "").strip()
        if audit_status:
            item["audit_status"] = audit_status

        row_extra = _loads_json(row.get("extra"))
        if not isinstance(row_extra, dict):
            item["edit_status"] = item.get("edit_status") or EDIT_STATUS_UNEDITED
            continue

        current_extra = item.get("extra")
        merged_extra = current_extra if isinstance(current_extra, dict) else {}
        merged_extra.update(row_extra)
        item["extra"] = merged_extra
        item["edit_status"] = str(row_extra.get("edit_status") or item.get("edit_status") or EDIT_STATUS_UNEDITED)

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
                    items = payload.get("items") if isinstance(payload.get("items"), list) else []
                else:
                    items = []

                return {
                    src: {
                        **(payload if isinstance(payload, dict) else {}),
                        "rows": items,
                    }
                }

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
                    items = payload.get("items") if isinstance(payload.get("items"), list) else []
                    result[src] = {
                        **payload,
                        "rows": items,
                    }
                else:
                    result[src] = {
                        "rows": [],
                    }

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
    company_certificate = parsed.get("company_certificate") if isinstance(parsed.get("company_certificate"), list) else []
    company_info = _normalize_company_info_fields(company_info)
    if (not isinstance(company_info.get("certificates"), list) or len(company_info.get("certificates") or []) == 0) and company_certificate:
        company_info["certificates"] = [x for x in company_certificate if isinstance(x, dict)]

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

    for key in (
        "employee_count",
        "operating_revenue",
        "insured_count",
    ):
        val = company_info.get(key)
        if val is not None:
            patch[key] = str(val).strip() or None

    certificates = company_info.get("certificates") if isinstance(company_info.get("certificates"), list) else None
    if certificates is not None:
        patch["certificates"] = certificates
    patch["edit_status"] = EDIT_STATUS_EDITED

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
                    "ai_company_info": company_info,
                    "ai_image_urls": recognition_payload.get("image_urls") or [],
                    "ai_model": recognition_payload.get("model"),
                    "ai_raw_text": recognition_payload.get("raw_text"),
                    "ai_updated_at": datetime.now().isoformat(timespec="seconds"),
                    "edit_status": EDIT_STATUS_EDITED,
                }
            )

            cur.execute("UPDATE entity_item SET extra = %s WHERE item_id = %s", (_to_json(merged_extra), item_id))

            if patch.get("issuer_full_name"):
                cur.execute(
                    "UPDATE entity_item SET issuer_full_name = %s WHERE item_id = %s",
                    (patch.get("issuer_full_name"), item_id),
                )

            cur.execute(
                "DELETE FROM entity_kv WHERE item_id = %s AND (field_key LIKE 'ai_%%' OR field_key = 'edit_status')",
                (item_id,),
            )

            ai_kv_rows = [
                ("edit_status", EDIT_STATUS_EDITED),
                ("ai_company_info", company_info if isinstance(company_info, dict) else None),
                ("ai_employee_count", company_info.get("employee_count") if isinstance(company_info, dict) else None),
                ("ai_operating_revenue", company_info.get("operating_revenue") if isinstance(company_info, dict) else None),
                ("ai_insured_count", company_info.get("insured_count") if isinstance(company_info, dict) else None),
                ("ai_certificates", company_info.get("certificates") if isinstance(company_info, dict) else None),
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

            company_profile_id = _upsert_company_profile(
                cur,
                item_id=item_id,
                source_url=safe_source_url,
                notice_url=safe_notice_url,
                parsed=parsed,
                company_info=company_info,
            )
            company_registry_profile_id = _upsert_company_registry_profile(
                cur,
                company_profile_id=company_profile_id,
                item_id=item_id,
                parsed=parsed,
            )
            certificate_count = _upsert_company_certificates(
                cur,
                company_profile_id=company_profile_id,
                item_id=item_id,
                parsed=parsed,
                company_info=company_info,
            )

            patch["employee_count"] = str(company_info.get("employee_count") or "").strip() or None
            patch["operating_revenue"] = str(company_info.get("operating_revenue") or "").strip() or None
            patch["insured_count"] = str(company_info.get("insured_count") or "").strip() or None

        conn.commit()

    return {
        "item_id": item_id,
        "item_patch": patch,
        "company_info": company_info,
        "company_profile_id": company_profile_id,
        "company_registry_profile_id": company_registry_profile_id,
        "certificate_count": certificate_count,
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
                  ei.audit_status,
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
                "audit_status": str(row.get("audit_status") or "").strip(),
                "item_date": item_date.isoformat() if item_date else "",
                "company_info": company_info,
                "extra": extra_obj,
            }
        )

    return result


def _normalize_lead_grade(value: Any) -> str:
    grade = str(value or "").strip().upper()
    return grade if grade in {"A", "B", "C", "D"} else "D"


def replace_ai_analysis_results(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = _mysql_config()

    total_count = int(summary.get("total") or len(rows) or 0)
    chunk_size = int(summary.get("chunk_size") or 0)
    chunk_count = int(summary.get("chunks") or 0)
    generated_at_raw = str(summary.get("generated_at") or "").strip()

    generated_at = datetime.now()
    if generated_at_raw:
        try:
            generated_at = datetime.fromisoformat(generated_at_raw.replace("Z", "+00:00"))
        except ValueError:
            generated_at = datetime.now()

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            # User requested replace semantics: clear old AI analysis then insert new.
            cur.execute("DELETE FROM ai_analysis_result")
            cur.execute("DELETE FROM ai_analysis_run")

            cur.execute(
                """
                INSERT INTO ai_analysis_run (
                  total_count, chunk_size, chunk_count, generated_at, summary_payload
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    total_count,
                    chunk_size,
                    chunk_count,
                    generated_at,
                    _to_json(summary),
                ),
            )
            run_id = int(cur.lastrowid)

            for idx, row in enumerate(rows):
                cur.execute(
                    """
                    INSERT INTO ai_analysis_result (
                      run_id, grade, company_name, contact_name, phone, email, employee_count, operating_revenue, insured_count, certificate_names, certificate_industries, reason, title, item_date, sort_order
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        _normalize_lead_grade(row.get("grade")),
                        (str(row.get("company_name") or "").strip() or None),
                        (str(row.get("contact_name") or "").strip() or None),
                        (str(row.get("phone") or "").strip() or None),
                        (str(row.get("email") or "").strip() or None),
                        (str(row.get("employee_count") or "").strip() or None),
                        (str(row.get("operating_revenue") or "").strip() or None),
                        (str(row.get("insured_count") or "").strip() or None),
                        (str(row.get("certificate_names") or "").strip() or None),
                        (str(row.get("certificate_industries") or "").strip() or None),
                        (str(row.get("reason") or "").strip() or None),
                        (str(row.get("title") or "").strip() or None),
                        _parse_date(row.get("item_date")),
                        idx,
                    ),
                )

        conn.commit()

    return {
        "run_id": run_id,
        "total": total_count,
    }


def load_latest_ai_analysis_results() -> dict[str, Any]:
    cfg = _mysql_config()

    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, total_count, chunk_size, chunk_count, generated_at, summary_payload
                FROM ai_analysis_run
                ORDER BY run_id DESC
                LIMIT 1
                """
            )
            run_row = cur.fetchone()
            if not run_row:
                return {
                    "summary": {
                        "total": 0,
                        "chunk_size": 0,
                        "chunks": 0,
                        "counts": {"A": 0, "B": 0, "C": 0, "D": 0},
                        "generated_at": "",
                    },
                    "groups": {"A": [], "B": [], "C": [], "D": []},
                }

            run_id = int(run_row.get("run_id") or 0)
            summary_payload = _loads_json(run_row.get("summary_payload"))

            cur.execute(
                """
                SELECT id, grade, company_name, contact_name, phone, email, employee_count, operating_revenue, insured_count, certificate_names, certificate_industries, reason, title, item_date
                FROM ai_analysis_result
                WHERE run_id = %s
                ORDER BY sort_order ASC, id ASC
                """,
                (run_id,),
            )
            rows = cur.fetchall() or []

    groups: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": [], "D": []}
    for row in rows:
        grade = _normalize_lead_grade(row.get("grade"))
        item_date = row.get("item_date")
        groups[grade].append(
            {
                "grade": grade,
                "company_name": str(row.get("company_name") or ""),
                "contact_name": str(row.get("contact_name") or ""),
                "phone": str(row.get("phone") or ""),
                "email": str(row.get("email") or ""),
                "employee_count": str(row.get("employee_count") or ""),
                "operating_revenue": str(row.get("operating_revenue") or ""),
                "insured_count": str(row.get("insured_count") or ""),
                "certificate_names": str(row.get("certificate_names") or ""),
                "certificate_industries": str(row.get("certificate_industries") or ""),
                "reason": str(row.get("reason") or ""),
                "title": str(row.get("title") or ""),
                "item_date": item_date.isoformat() if item_date else "",
            }
        )

    counts = {grade: len(groups[grade]) for grade in ("A", "B", "C", "D")}
    generated_at_obj = run_row.get("generated_at")
    generated_at = generated_at_obj.isoformat() if generated_at_obj else ""

    summary = {
        "total": int(run_row.get("total_count") or len(rows)),
        "chunk_size": int(run_row.get("chunk_size") or 0),
        "chunks": int(run_row.get("chunk_count") or 0),
        "counts": counts,
        "generated_at": generated_at,
    }

    if isinstance(summary_payload, dict):
        summary = {
            "total": int(summary_payload.get("total") or summary["total"]),
            "chunk_size": int(summary_payload.get("chunk_size") or summary["chunk_size"]),
            "chunks": int(summary_payload.get("chunks") or summary["chunks"]),
            "counts": counts,
            "generated_at": str(summary_payload.get("generated_at") or summary["generated_at"]),
        }

    return {
        "summary": summary,
        "groups": groups,
    }
