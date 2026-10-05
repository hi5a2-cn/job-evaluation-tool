import hashlib
import sqlite3
from typing import Any, Sequence

from jet.db.store import transaction
from jet.domain.normalize import normalize_text
from jet.domain.salary import parse_salary


def compute_content_hash(
    title: str,
    salary_raw: str | None,
    city: str,
    description: str | None,
) -> str:
    """Compute sha256 hash of normalized substantial job fields."""
    norm_title = normalize_text(title)
    norm_salary = normalize_text(salary_raw) if (salary_raw and salary_raw.strip()) else "<invisible>"
    norm_city = normalize_text(city)
    norm_desc = normalize_text(description) if (description and description.strip()) else ""
    payload = f"{norm_title}|{norm_salary}|{norm_city}|{norm_desc}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def ingest(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    page_type: str,
    jobs: Sequence[Any],
    observed_at: str,
) -> dict[str, sqlite3.Row]:
    """
    Ingest observed jobs from a list or detail page into the jobs, job_versions, and views tables.

    Returns a dict mapping platform_job_id -> job row in jobs table.
    """
    result: dict[str, sqlite3.Row] = {}

    def _execute_ingest():
        for job_item in jobs:
            platform_job_id = str(_get_field(job_item, "platform_job_id") or "").strip()
            title = str(_get_field(job_item, "title") or "").strip()
            raw_salary = _get_field(job_item, "salary_raw")
            if raw_salary is not None:
                raw_salary = str(raw_salary).strip()
                if not raw_salary:
                    raw_salary = None

            city = str(_get_field(job_item, "city") or "").strip()
            raw_district = _get_field(job_item, "district")
            district = str(raw_district).strip() if (raw_district and str(raw_district).strip()) else None

            raw_desc = _get_field(job_item, "description")
            description = str(raw_desc).strip() if (raw_desc and str(raw_desc).strip()) else None

            raw_company_name = _get_field(job_item, "company_name")
            company_name = str(raw_company_name).strip() if (raw_company_name and str(raw_company_name).strip()) else None

            raw_company_industry = _get_field(job_item, "company_industry")
            company_industry = (
                str(raw_company_industry).strip()[:50]
                if (raw_company_industry and str(raw_company_industry).strip())
                else None
            )

            raw_company_legal_name = _get_field(job_item, "company_legal_name")
            company_legal_name = (
                str(raw_company_legal_name).strip()[:100]
                if (raw_company_legal_name and str(raw_company_legal_name).strip())
                else None
            )

            raw_experience = _get_field(job_item, "experience")
            if raw_experience is None:
                raw_experience = _get_field(job_item, "experience_req")
            experience_req = (
                str(raw_experience).strip()[:20]
                if (raw_experience and str(raw_experience).strip())
                else None
            )

            raw_degree = _get_field(job_item, "degree")
            if raw_degree is None:
                raw_degree = _get_field(job_item, "degree_req")
            degree_req = (
                str(raw_degree).strip()[:20]
                if (raw_degree and str(raw_degree).strip())
                else None
            )

            # Salary parsing
            if raw_salary is not None:
                salary_visible = 1
                parsed = parse_salary(raw_salary)
                salary_min_k = parsed.min_k if parsed.parse_ok else None
                salary_max_k = parsed.max_k if parsed.parse_ok else None
                salary_months = parsed.months if parsed.parse_ok else None
                salary_parse_ok = 1 if parsed.parse_ok else 0
            else:
                salary_visible = 0
                salary_min_k = None
                salary_max_k = None
                salary_months = None
                salary_parse_ok = 0

            # Check if job already exists
            existing_job = conn.execute(
                "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
                (platform_job_id,),
            ).fetchone()

            if existing_job is None:
                completeness = "full" if page_type == "detail" else "list_only"
                target_company_name = company_name if company_name else company_legal_name
                cur = conn.execute(
                    "INSERT INTO jobs (platform, platform_job_id, completeness, current_version_id, company_name, company_industry, experience_req, degree_req, first_seen_at, last_seen_at) "
                    "VALUES ('boss', ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                    (platform_job_id, completeness, target_company_name, company_industry, experience_req, degree_req, observed_at, observed_at),
                )
                job_id = cur.lastrowid
                content_hash = compute_content_hash(title, raw_salary, city, description)

                v_cur = conn.execute(
                    "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                    "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                    "content_hash, created_at) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        page_type,
                        title,
                        raw_salary,
                        salary_visible,
                        salary_min_k,
                        salary_max_k,
                        salary_months,
                        salary_parse_ok,
                        city,
                        district,
                        description,
                        content_hash,
                        observed_at,
                    ),
                )
                version_id = v_cur.lastrowid
                conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (version_id, job_id))

            else:
                job_id = existing_job["id"]
                if company_name:
                    conn.execute("UPDATE jobs SET company_name = ? WHERE id = ?", (company_name, job_id))
                elif company_legal_name:
                    # 已有岗位只在 company_name 为空时写入工商名（FR-014，research R7）
                    existing_cname = existing_job["company_name"]
                    if not (existing_cname and str(existing_cname).strip()):
                        conn.execute("UPDATE jobs SET company_name = ? WHERE id = ?", (company_legal_name, job_id))
                if company_industry:
                    conn.execute("UPDATE jobs SET company_industry = ? WHERE id = ?", (company_industry, job_id))
                if experience_req:
                    conn.execute("UPDATE jobs SET experience_req = ? WHERE id = ?", (experience_req, job_id))
                if degree_req:
                    conn.execute("UPDATE jobs SET degree_req = ? WHERE id = ?", (degree_req, job_id))
                if existing_job["current_version_id"] is None:
                    # 聊天等场景入库的无版本岗位，按首个版本补录（version_no=1）
                    if page_type == "list":
                        content_hash = compute_content_hash(title, raw_salary, city, None)
                        v_cur = conn.execute(
                            "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                            "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                            "content_hash, created_at) "
                            "VALUES (?, 1, 'list', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                            (
                                job_id,
                                title,
                                raw_salary,
                                salary_visible,
                                salary_min_k,
                                salary_max_k,
                                salary_months,
                                salary_parse_ok,
                                city,
                                district,
                                content_hash,
                                observed_at,
                            ),
                        )
                        new_version_id = v_cur.lastrowid
                        conn.execute(
                            "UPDATE jobs SET current_version_id = ?, last_seen_at = ? WHERE id = ?",
                            (new_version_id, observed_at, job_id),
                        )
                    else:  # page_type == "detail"
                        content_hash = compute_content_hash(title, raw_salary, city, description)
                        v_cur = conn.execute(
                            "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                            "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                            "content_hash, created_at) "
                            "VALUES (?, 1, 'detail', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                job_id,
                                title,
                                raw_salary,
                                salary_visible,
                                salary_min_k,
                                salary_max_k,
                                salary_months,
                                salary_parse_ok,
                                city,
                                district,
                                description,
                                content_hash,
                                observed_at,
                            ),
                        )
                        new_version_id = v_cur.lastrowid
                        conn.execute(
                            "UPDATE jobs SET completeness = 'full', current_version_id = ?, last_seen_at = ? WHERE id = ?",
                            (new_version_id, observed_at, job_id),
                        )
                else:
                    current_version = conn.execute(
                        "SELECT * FROM job_versions WHERE id = ?",
                        (existing_job["current_version_id"],),
                    ).fetchone()

                    title_match = normalize_text(title) == normalize_text(current_version["title"])
                    city_match = normalize_text(city) == normalize_text(current_version["city"])

                    salary_match = (salary_visible == current_version["salary_visible"]) and (
                        normalize_text(raw_salary) == normalize_text(current_version["salary_raw"])
                        if salary_visible
                        else True
                    )

                    if page_type == "list":
                        if existing_job["completeness"] == "full":
                            # 已是 full 的岗位从列表页读到时不比较、不新建版本，只更新最近读到时间，内容变化以详情页为准（Issue #6）
                            conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (observed_at, job_id))
                        # Only compare title, salary, city
                        elif title_match and city_match and salary_match:
                            # Unchanged: only update last_seen_at (do not downgrade completeness)
                            conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (observed_at, job_id))
                        else:
                            # Changed in list
                            new_version_no = current_version["version_no"] + 1
                            content_hash = compute_content_hash(title, raw_salary, city, None)
                            v_cur = conn.execute(
                                "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                                "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                                "content_hash, created_at) "
                                "VALUES (?, ?, 'list', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                                (
                                    job_id,
                                    new_version_no,
                                    title,
                                    raw_salary,
                                    salary_visible,
                                    salary_min_k,
                                    salary_max_k,
                                    salary_months,
                                    salary_parse_ok,
                                    city,
                                    district,
                                    content_hash,
                                    observed_at,
                                ),
                            )
                            new_version_id = v_cur.lastrowid
                            conn.execute(
                                "UPDATE jobs SET current_version_id = ?, last_seen_at = ? WHERE id = ?",
                                (new_version_id, observed_at, job_id),
                            )

                    else:  # page_type == "detail"
                        if existing_job["completeness"] == "list_only":
                            # Upgrading list_only to full: always creates detail version
                            new_version_no = current_version["version_no"] + 1
                            content_hash = compute_content_hash(title, raw_salary, city, description)
                            v_cur = conn.execute(
                                "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                                "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                                "content_hash, created_at) "
                                "VALUES (?, ?, 'detail', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    job_id,
                                    new_version_no,
                                    title,
                                    raw_salary,
                                    salary_visible,
                                    salary_min_k,
                                    salary_max_k,
                                    salary_months,
                                    salary_parse_ok,
                                    city,
                                    district,
                                    description,
                                    content_hash,
                                    observed_at,
                                ),
                            )
                            new_version_id = v_cur.lastrowid
                            conn.execute(
                                "UPDATE jobs SET completeness = 'full', current_version_id = ?, last_seen_at = ? WHERE id = ?",
                                (new_version_id, observed_at, job_id),
                            )
                        else:
                            # Existing is full: compare all 4 fields
                            desc_match = normalize_text(description) == normalize_text(current_version["description"])
                            if title_match and city_match and salary_match and desc_match:
                                conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (observed_at, job_id))
                            else:
                                new_version_no = current_version["version_no"] + 1
                                content_hash = compute_content_hash(title, raw_salary, city, description)
                                v_cur = conn.execute(
                                    "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, "
                                    "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, "
                                    "content_hash, created_at) "
                                    "VALUES (?, ?, 'detail', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        job_id,
                                        new_version_no,
                                        title,
                                        raw_salary,
                                        salary_visible,
                                        salary_min_k,
                                        salary_max_k,
                                        salary_months,
                                        salary_parse_ok,
                                        city,
                                        district,
                                        description,
                                        content_hash,
                                        observed_at,
                                    ),
                                )
                                new_version_id = v_cur.lastrowid
                                conn.execute(
                                    "UPDATE jobs SET current_version_id = ?, last_seen_at = ? WHERE id = ?",
                                    (new_version_id, observed_at, job_id),
                                )

            # Record view
            conn.execute(
                "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES (?, ?, ?, ?)",
                (user_id, job_id, page_type, observed_at),
            )

            # Fetch updated job row
            updated_job = conn.execute(
                "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
                (platform_job_id,),
            ).fetchone()
            result[platform_job_id] = updated_job

    if conn.in_transaction:
        _execute_ingest()
    else:
        with transaction(conn):
            _execute_ingest()

    return result
