import base64
import dataclasses
import hashlib
import math
import secrets
import sqlite3
from typing import Any, Literal
import urllib.parse
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, Field, field_validator

import jet
from jet.api.auth import require_paired
from jet.config import (
    delete_llm_key_file,
    get_llm_key_info,
    resolve_env_llm_key,
    save_llm_key_to_file,
)
from jet.db.store import get_conn, local_day_bounds_utc, transaction, utc_now
from jet.domain.consent import (
    CONSENT_FIELDS_VERSION,
    consent_status,
    has_valid_consent,
    record_consent,
    revoke_consent,
)
from jet.domain.experience import ExperienceError, list_experiences, replace_experiences
from jet.domain.hr_notes import hr_note_for, save_hr_note
from jet.domain.resume import (
    FileTooLargeError,
    NoTextError,
    PdfInvalidError,
    ResumeError,
    ResumeInvalidError,
    ResumeNotFoundError,
    ResumeSlotInvalidError,
    SelfNameRequiredError,
    delete_resume_slot,
    extract_text_from_pdf_bytes,
    get_resume_slot,
    list_resumes,
    sanitize_resume_text,
    save_resume_upload,
    save_resumes,
)
from jet.llm.resume_profile import generate_resume_profile
from jet.domain.industry import (
    InvalidIndustryError,
    get_user_strict_industries,
    read_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
    save_user_strict_industries,
)
from jet.domain.job_status import list_my_jobs, set_status, status_for
from jet.domain.jobs import ingest
from jet.domain.judgements import rule_excluded_today, latest_judgement, rejudge, request_judgement, to_api
from jet.domain.labels import label_counts, save_label
from jet.domain.profiles import get_current_profile, save_profile
from jet.domain.rules import screen_hints
from jet.llm.assist import (
    AllSuggestionsDroppedError,
    HashMismatchError,
    assemble_assist_request,
    assist_preview_fields,
    generate_assist_suggestions,
)
from jet.llm.client import HttpError, NotSent, ParseError, RetryableHttp, Timeout, call_once
from jet.llm.prejudge import build_prejudge_messages, parse_prejudge_response
from jet.llm.quota import finish, remaining_today, reserve, usage_today
from jet.llm.sanitize import SelfNameUnavailable

router = APIRouter()


def _llm_transport(request: Request) -> httpx.BaseTransport | None:
    """测试里注入的大模型传输对象：先看后台队列（worker）的，再看应用级的；正式运行时都为 None，走真实网络。"""
    worker = getattr(request.app.state, "worker", None)
    transport = getattr(worker, "transport", None) if worker is not None else None
    if transport is None:
        transport = getattr(request.app.state, "llm_transport", None)
    return transport


class PrejudgeJobItem(BaseModel):
    platform_job_id: str
    title: str
    company_name: str | None = None
    company_industry: str | None = None
    salary_raw: str | None = None
    city: str = ""
    district: str | None = None
    experience: str | None = None
    degree: str | None = None
    job_labels: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class PrejudgeRequest(BaseModel):
    jobs: list[PrejudgeJobItem] = Field(..., max_length=40)


class PrejudgeSettingsPayload(BaseModel):
    daily_prejudge_limit: int = Field(..., ge=0, le=200)


class PairRequest(BaseModel):
    code: str


class PutLlmKeyRequest(BaseModel):
    api_key: str


class TestLlmKeyRequest(BaseModel):
    api_key: str | None = None


class JudgeRequest(BaseModel):
    force: bool = False


class HrNotePayload(BaseModel):
    note: str | None = None
    source: str | None = "card"


class JobStatusPayload(BaseModel):
    status: Literal["saved", "applied", "skipped"] | None = None


class ChatJobRequest(BaseModel):
    platform_job_id: str
    title: str
    company_name: str | None = None


class ProfilePayload(BaseModel):
    directions: list[str]
    keywords: list[str] = []
    preferred_cities: list[str] | None = None
    cities: list[str] | None = None
    excluded_cities: list[str] = []
    min_monthly_k: float | None = None
    nonpref_min_monthly_k: float | None = None
    exclude_keywords: list[str] = []
    work_preference: str = ""
    background: str = ""
    current_city: str = ""


class LabelPayload(BaseModel):
    work_type: str | None = None
    work_subtype: str | None = None
    secondary_work_types: list[Any] | None = None
    sales_level: str | None = None
    experience_fit: str | None = None
    work_intensity: str | None = None
    overtime: str | None = None
    overall: str | None = None
    note: str | None = None


class ObservationJob(BaseModel):
    platform_job_id: str
    title: str
    company_name: str | None = None
    company_legal_name: str | None = None
    company_industry: str | None = Field(default=None, max_length=50)
    experience: str | None = Field(default=None, max_length=20)
    degree: str | None = Field(default=None, max_length=20)
    salary_raw: str | None = None
    city: str
    district: str | None = None
    description: str | None = None
    job_labels: list[str] | None = None
    skills: list[str] | None = None

    @field_validator("company_legal_name", mode="before")
    @classmethod
    def _truncate_company_legal_name(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            return v[:100]
        return str(v)[:100]

    @field_validator("job_labels", "skills", mode="before")
    @classmethod
    def _truncate_labels(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            res: list[str] = []
            for item in v[:20]:
                if isinstance(item, str):
                    res.append(item[:30])
                elif item is not None:
                    res.append(str(item)[:30])
            return res
        return v


def _job_entry(
    conn: sqlite3.Connection,
    user_id: str,
    job_row: sqlite3.Row,
) -> dict[str, Any]:
    """Build canonical job entry dictionary with completeness, company_name, title, judgement, hr_note, my_status, and seen_in_chat."""
    latest = latest_judgement(conn, user_id, job_row["id"])
    hr_note = hr_note_for(conn, user_id, job_row["id"])
    my_st = status_for(conn, user_id, job_row["id"])
    cur_version_id = job_row["current_version_id"] if "current_version_id" in job_row.keys() else None
    v_row = (
        conn.execute(
            "SELECT title FROM job_versions WHERE id = ?",
            (cur_version_id,),
        ).fetchone()
        if cur_version_id
        else None
    )
    title = v_row["title"] if v_row else None

    # 查询该用户是否在聊天中遇到过该岗位，并在 current_version_id 为 NULL 时回退职位名
    chat_row = conn.execute(
        "SELECT chat_title FROM job_chat_seen WHERE user_id = ? AND job_id = ?",
        (user_id, job_row["id"]),
    ).fetchone()
    seen_in_chat = chat_row is not None
    if not title and chat_row and chat_row["chat_title"]:
        title = chat_row["chat_title"]

    return {
        "completeness": job_row["completeness"],
        "company_name": job_row.get("company_name"),
        "title": title,
        "judgement": to_api(conn, latest) if latest else None,
        "hr_note": hr_note,
        "my_status": my_st,
        "seen_in_chat": seen_in_chat,
    }


class ObservationRequest(BaseModel):
    page_type: Literal["list", "detail"]
    observed_at: str
    jobs: list[ObservationJob]


@router.get("/v1/health")
def health() -> dict:
    """Check service health and version."""
    return {
        "service": "jet",
        "api": 1,
        "version": jet.__version__,
    }


@router.post("/v1/pair")
def pair(body: PairRequest, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Pair a browser extension using a 6-digit one-time pairing code."""
    origin = request.headers.get("origin")
    if not origin or not origin.startswith("chrome-extension://"):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_origin", "message": "无效或缺失的 Origin"},
        )

    pairing_codes = request.app.state.pairing_codes
    verify_result = pairing_codes.verify(body.code)

    if verify_result == "bad_code":
        return JSONResponse(
            status_code=400,
            content={"error": "bad_code", "message": "配对码错误或已过期"},
        )
    if verify_result == "too_many_attempts":
        return JSONResponse(
            status_code=429,
            content={"error": "too_many_attempts", "message": "尝试次数过多，请重新运行 jet pair"},
        )

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    now_str = utc_now()
    conn.execute(
        "INSERT INTO pairings (user_id, extension_origin, token_hash, created_at, last_used_at, revoked_at) "
        "VALUES ('me', ?, ?, ?, ?, NULL)",
        (origin, token_hash, now_str, now_str),
    )

    return {"token": token, "user_id": "me"}


@router.get("/v1/status")
def status(
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve profile and quota status for the paired user."""
    # 1. Profile status
    prof = get_current_profile(conn, user_id)
    if prof:
        profile_info = {"set": True, "version_no": prof["version_no"]}
    else:
        profile_info = {"set": False, "version_no": None}

    # 2. Quota status (judge and assist)
    quota_info = usage_today(conn, user_id, purpose="judge")
    assist_quota_info = usage_today(conn, user_id, purpose="assist")

    # 3. Rule excluded count
    rule_excluded = rule_excluded_today(conn, user_id)

    # 4. Labels count
    l_counts = label_counts(conn, user_id)

    return {
        "user_id": user_id,
        "profile": profile_info,
        "quota": quota_info,
        "assist_quota": assist_quota_info,
        "rule_excluded_today": rule_excluded,
        "labels": l_counts,
        "llm_key_configured": bool(request.app.state.settings.llm_api_key),
    }


@router.get("/v1/llm-key")
def get_llm_key(
    request: Request,
    user_id: str = Depends(require_paired),
):
    """Retrieve API Key configured status, source, and masked display."""
    return get_llm_key_info(request.app.state.settings)


@router.put("/v1/llm-key")
def put_llm_key(
    body: PutLlmKeyRequest,
    request: Request,
    user_id: str = Depends(require_paired),
):
    """Save API Key to data directory, setting 0600 permissions and updating settings in memory."""
    key = body.api_key.strip()
    if not key:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": "API Key 不能为空"},
        )

    save_llm_key_to_file(request.app.state.settings.data_dir, key)
    new_settings = dataclasses.replace(
        request.app.state.settings,
        llm_api_key=key,
        llm_key_source="settings_page",
    )
    request.app.state.settings = new_settings
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.settings = new_settings

    return get_llm_key_info(new_settings)


@router.delete("/v1/llm-key")
def delete_llm_key(
    request: Request,
    user_id: str = Depends(require_paired),
):
    """Delete API Key from data directory and fallback to .env or env if available."""
    delete_llm_key_file(request.app.state.settings.data_dir)
    fallback_key = resolve_env_llm_key(request.app.state.settings.data_dir)
    new_source = "env" if fallback_key else None
    new_settings = dataclasses.replace(
        request.app.state.settings,
        llm_api_key=fallback_key,
        llm_key_source=new_source,
    )
    request.app.state.settings = new_settings
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.settings = new_settings

    return get_llm_key_info(new_settings)


@router.post("/v1/llm-key/test")
def test_llm_key(
    request: Request,
    body: TestLlmKeyRequest | None = Body(default=None),
    user_id: str = Depends(require_paired),
):
    """Test connectivity and key validity against GET {base_url}/models (timeout 10s, 0 quota, 0 llm_calls)."""
    raw_key = body.api_key.strip() if (body and body.api_key) else None
    key_to_test = raw_key or request.app.state.settings.llm_api_key
    if not key_to_test:
        return {"ok": False, "reason": "no_key"}

    settings = request.app.state.settings
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {key_to_test}",
    }
    transport = _llm_transport(request)

    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            resp = client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.RequestError):
        return {"ok": False, "reason": "unreachable"}
    except Exception:
        return {"ok": False, "reason": "unreachable"}

    if 200 <= resp.status_code < 300:
        return {"ok": True, "reason": "ok"}
    elif resp.status_code in (401, 403):
        return {"ok": False, "reason": "invalid_key"}
    else:
        return {"ok": False, "reason": "unreachable"}


@router.get("/v1/profile")
def get_profile(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Get current profile for the paired user."""
    prof = get_current_profile(conn, user_id)
    if not prof:
        return JSONResponse(
            status_code=404,
            content={"error": "no_profile", "message": "尚未设置画像"},
        )
    return {
        "version_no": prof["version_no"],
        "directions": prof["directions"],
        "keywords": prof["keywords"],
        "cities": prof["cities"],
        "preferred_cities": prof["preferred_cities"],
        "excluded_cities": prof["excluded_cities"],
        "min_monthly_k": prof["min_monthly_k"],
        "nonpref_min_monthly_k": prof["nonpref_min_monthly_k"],
        "exclude_keywords": prof["exclude_keywords"],
        "work_preference": prof.get("work_preference", ""),
        "background": prof.get("background", ""),
        "current_city": prof.get("current_city", ""),
    }


@router.put("/v1/profile")
def update_profile(
    body: ProfilePayload,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save or update profile for the paired user."""
    try:
        version_no, changed = save_profile(conn, user_id, body.model_dump())
    except ValueError as e:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": str(e)},
        )
    return {"version_no": version_no, "changed": changed}


@router.post("/v1/observations")
def observations(
    body: ObservationRequest,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Ingest job observations and trigger judgements for detail observations."""
    if not (1 <= len(body.jobs) <= 200):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": "岗位数量必须在 1 到 200 之间"},
        )

    for job in body.jobs:
        if not job.platform_job_id.strip() or not job.title.strip() or not job.city.strip():
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": "岗位必须包含非空的 platform_job_id、title 和 city"},
            )

    if body.page_type == "detail":
        if len(body.jobs) != 1:
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": "详情页必须且只能包含 1 个岗位"},
            )
        if not body.jobs[0].description or not body.jobs[0].description.strip():
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": "详情页岗位必须包含非空的 description"},
            )

    newly_queued_judgements: list[int] = []
    notice: str | None = None
    jobs_dict: dict[str, Any] = {}

    with transaction(conn, immediate=True):  # 先拿写锁，避免 WAL 下读后升级写锁失败
        job_rows = ingest(
            conn,
            user_id=user_id,
            page_type=body.page_type,
            jobs=body.jobs,
            observed_at=body.observed_at,
        )

        if body.page_type == "detail":
            prof = get_current_profile(conn, user_id)
            if prof is None:
                notice = "no_profile"
            else:
                target_job = job_rows[body.jobs[0].platform_job_id]
                has_llm_key = bool(request.app.state.settings.llm_api_key)
                j_row, is_queued, req_notice = request_judgement(
                    conn,
                    user_id=user_id,
                    job_row=target_job,
                    has_llm_key=has_llm_key,
                    prompt_version=request.app.state.settings.prompt_version,
                )
                if req_notice:
                    notice = req_notice
                if is_queued and j_row:
                    newly_queued_judgements.append(j_row["id"])

        list_prof = None
        strict_industries: list[str] = []
        strict_aliases: dict[str, list[str]] = {}
        strict_keywords: dict[str, list[str]] = {}
        if body.page_type == "list":
            list_prof = get_current_profile(conn, user_id)
            user_selected = get_user_strict_industries(conn, user_id)
            if user_selected:
                strict_industries = user_selected
                all_aliases = read_strict_industry_aliases()
                all_keywords = read_strict_industry_keywords()
                strict_aliases = {k: all_aliases[k] for k in user_selected if k in all_aliases}
                strict_keywords = {k: all_keywords[k] for k in user_selected if k in all_keywords}

    # Hand off newly queued judgements to background worker
    worker = getattr(request.app.state, "worker", None)
    if worker:
        for jid in newly_queued_judgements:
            worker.submit(jid)

    for job in body.jobs:
        pid = job.platform_job_id
        j_row = job_rows[pid]
        entry = _job_entry(conn, user_id, j_row)
        if body.page_type == "list":
            cur_version_id = j_row["current_version_id"] if "current_version_id" in j_row.keys() else None
            v_row = (
                conn.execute(
                    "SELECT * FROM job_versions WHERE id = ?",
                    (cur_version_id,),
                ).fetchone()
                if cur_version_id
                else None
            )
            c_ind = (
                j_row["company_industry"]
                if ("company_industry" in j_row.keys() and j_row["company_industry"])
                else job.company_industry
            )
            c_name = (
                j_row["company_name"]
                if ("company_name" in j_row.keys() and j_row["company_name"])
                else job.company_name
            )
            tags = list(job.job_labels or []) + list(job.skills or [])
            entry["screen_hints"] = screen_hints(
                job_version=v_row,
                profile=list_prof,
                company_industry=c_ind,
                tags=tags,
                industries=strict_industries,
                aliases=strict_aliases,
                company_name=c_name,
                keywords=strict_keywords,
            )
        jobs_dict[pid] = entry

    return {"jobs": jobs_dict, "notice": notice}


@router.post("/v1/chat/job")
def post_chat_job(
    body: ChatJobRequest,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Ingest a job passively observed in chat without triggering judgement or rule checks."""
    pid = body.platform_job_id.strip() if body.platform_job_id else ""
    title = body.title.strip() if body.title else ""
    if not pid or not title:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": "岗位必须包含非空的 platform_job_id 和 title"},
        )

    company_name = body.company_name.strip() if (body.company_name and body.company_name.strip()) else None
    now_str = utc_now()

    with transaction(conn, immediate=True):
        existing_job = conn.execute(
            "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (pid,),
        ).fetchone()

        if existing_job is None:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    platform, platform_job_id, completeness, current_version_id,
                    company_name, first_seen_at, last_seen_at
                )
                VALUES ('boss', ?, 'list_only', NULL, ?, ?, ?)
                """,
                (pid, company_name, now_str, now_str),
            )
            job_id = cur.lastrowid
        else:
            job_id = existing_job["id"]
            existing_company = existing_job["company_name"]
            if (existing_company is None or not str(existing_company).strip()) and company_name:
                conn.execute(
                    "UPDATE jobs SET company_name = ?, last_seen_at = ? WHERE id = ?",
                    (company_name, now_str, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE id = ?",
                    (now_str, job_id),
                )

        conn.execute(
            """
            INSERT INTO job_chat_seen (user_id, job_id, chat_title, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                chat_title = CASE
                    WHEN length(trim(job_chat_seen.chat_title)) = 0 THEN excluded.chat_title
                    ELSE job_chat_seen.chat_title
                END
            """,
            (user_id, job_id, title, now_str, now_str),
        )

        job_row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

        entry = _job_entry(conn, user_id, job_row)

    return {
        "jobs": {
            pid: entry,
        }
    }


@router.get("/v1/judgements")
def get_judgements(
    request: Request,
    ids: str = Query(default=""),
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve current judgements for specified platform job IDs."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    jobs_dict: dict[str, Any] = {}

    for pid in id_list:
        job = conn.execute(
            "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (pid,),
        ).fetchone()
        if job is not None:
            jobs_dict[pid] = _job_entry(conn, user_id, job)

    return {"jobs": jobs_dict}


@router.post("/v1/jobs/{platform_job_id}/judge")
def judge_job(
    platform_job_id: str,
    request: Request,
    body: JudgeRequest | None = Body(default=None),
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Trigger manual re-judgement for a specific platform job."""
    force = body.force if body is not None else False
    has_llm_key = bool(request.app.state.settings.llm_api_key)
    j_row, is_queued = rejudge(
        conn,
        user_id=user_id,
        platform_job_id=platform_job_id,
        force=force,
        has_llm_key=has_llm_key,
        prompt_version=request.app.state.settings.prompt_version,
    )

    worker = getattr(request.app.state, "worker", None)
    if worker and is_queued:
        worker.submit(j_row["id"])

    job = conn.execute(
        "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (platform_job_id,),
    ).fetchone()

    return {
        "jobs": {
            platform_job_id: _job_entry(conn, user_id, job)
        }
    }


@router.put("/v1/jobs/{platform_job_id}/label")
def put_label(
    platform_job_id: str,
    body: LabelPayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save or update label for the job's current version."""
    with transaction(conn, immediate=True):
        try:
            save_label(
                conn,
                user_id=user_id,
                platform_job_id=platform_job_id,
                data=body.model_dump(),
            )
        except ValueError as e:
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": str(e)},
            )

        job = conn.execute(
            "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (platform_job_id,),
        ).fetchone()

        entry = _job_entry(conn, user_id, job)
        counts = label_counts(conn, user_id)

    return {
        "jobs": {
            platform_job_id: entry,
        },
        "labels": counts,
    }


@router.put("/v1/jobs/{platform_job_id}/hr-note")
def put_hr_note(
    platform_job_id: str,
    body: HrNotePayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save, update, or clear HR note for a platform job."""
    source = body.source or "card"
    if (source in ("myjobs", "chat_sidebar")) and (body.note is None or not body.note.strip()):
        return JSONResponse(
            status_code=422,
            content={"error": "empty_not_allowed", "message": "清空请到岗位卡片操作"},
        )

    with transaction(conn, immediate=True):
        try:
            save_hr_note(
                conn,
                user_id=user_id,
                platform_job_id=platform_job_id,
                note=body.note,
            )
        except ValueError as e:
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": str(e)},
            )

        job = conn.execute(
            "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (platform_job_id,),
        ).fetchone()

        entry = _job_entry(conn, user_id, job)

    return {
        "jobs": {
            platform_job_id: entry,
        }
    }


@router.put("/v1/jobs/{platform_job_id}/status")
def put_job_status(
    platform_job_id: str,
    body: JobStatusPayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Set, update, or clear personal status for a platform job."""
    with transaction(conn, immediate=True):
        try:
            set_status(
                conn,
                user_id=user_id,
                platform_job_id=platform_job_id,
                status=body.status,
            )
        except LookupError:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "岗位不存在"},
            )
        except ValueError as e:
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_payload", "message": str(e)},
            )

        job = conn.execute(
            "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (platform_job_id,),
        ).fetchone()

        entry = _job_entry(conn, user_id, job)

    return {
        "jobs": {
            platform_job_id: entry,
        }
    }


@router.get("/v1/my-jobs")
def get_my_jobs(
    request: Request,
    filter: str = Query(...),
    hr_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=200),
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """List jobs in user's library with counts and filtered items."""
    if filter not in ("all_jobs", "all", "saved", "applied", "skipped", "recent"):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": f"无效的 filter: {filter}"},
        )
    if limit < 1:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": "limit 必须大于 0"},
        )

    return list_my_jobs(
        conn,
        user_id=user_id,
        filter=filter,
        hr_only=hr_only,
        q=q,
        limit=limit,
    )


@router.post("/internal/pair-code")
def internal_pair_code(request: Request):
    """Internal loopback-only endpoint to generate a pairing code for 'jet pair' CLI."""
    client_host = request.client.host if request.client else None
    if client_host != "127.0.0.1":
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "只允许本机访问"},
        )

    if "origin" in request.headers:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "内部接口禁止携带 Origin"},
        )

    admin_header = request.headers.get("x-jet-admin")
    expected_secret = getattr(request.app.state, "admin_secret", None)
    if not expected_secret or admin_header != expected_secret:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "管理密钥错误"},
        )

    code = request.app.state.pairing_codes.issue()
    return {"code": code, "expires_in": 300}


@router.post("/v1/chat/consent")
def post_chat_consent(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Record user consent to transmit sanitized chat data to LLM."""
    with transaction(conn, immediate=True):
        consented_at = record_consent(conn, user_id, fields_version=CONSENT_FIELDS_VERSION)
    return {
        "ok": True,
        "consented_at": consented_at,
    }


@router.post("/v1/chat/revoke-consent")
def post_chat_revoke_consent(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Revoke user consent to transmit chat data to LLM."""
    with transaction(conn, immediate=True):
        revoked_at = revoke_consent(conn, user_id)
    return {
        "ok": True,
        "revoked_at": revoked_at,
    }


@router.get("/v1/chat/consent-status")
def get_chat_consent_status(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve user consent status for chat data transmission."""
    return consent_status(conn, user_id)


@router.get("/v1/experience")
def get_experience(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve all experience items for the paired user."""
    return list_experiences(conn, user_id)


@router.put("/v1/experience")
def put_experience(
    body: Any = Body(...),
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save and replace all experience items for the paired user."""
    try:
        with transaction(conn, immediate=True):
            count = replace_experiences(conn, user_id, body)
    except ExperienceError as e:
        return JSONResponse(
            status_code=422,
            content={"error": e.error, "message": e.message},
        )
    except ValueError as e:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": str(e)},
        )
    return {"ok": True, "count": count}


class ResumeUploadPayload(BaseModel):
    slot: Literal[1, 2, 3]
    name: str = Field(..., max_length=100)
    pdf_base64: str
    self_name: str | None = None


class ResumeRegeneratePayload(BaseModel):
    self_name: str | None = None


@router.post("/v1/resumes/upload")
def post_resume_upload(
    body: ResumeUploadPayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """上传 PDF 并在本机脱敏后生成简历画像 (FR-010, T011)。"""
    clean_name = body.name.strip()
    if not clean_name:
        return JSONResponse(
            status_code=422,
            content={"error": "resume_invalid", "message": "简历名称不能为空"},
        )
    if len(clean_name) > 100:
        return JSONResponse(
            status_code=422,
            content={"error": "resume_invalid", "message": "简历名称不能超过 100 字"},
        )

    # 1. Base64 解码前大小校验 (≤ 5MB 对应 Base64 长度) 与合法性校验
    max_b64_len = math.ceil(5 * 1024 * 1024 / 3) * 4
    if len(body.pdf_base64) > max_b64_len:
        return JSONResponse(
            status_code=422,
            content={"error": "file_too_large", "message": "PDF 文件大小不能超过 5MB"},
        )

    try:
        pdf_bytes = base64.b64decode(body.pdf_base64, validate=True)
    except Exception:
        return JSONResponse(
            status_code=422,
            content={"error": "pdf_invalid", "message": "PDF 文件已加密或已损坏，无法解析"},
        )

    if len(pdf_bytes) > 5 * 1024 * 1024:
        return JSONResponse(
            status_code=422,
            content={"error": "file_too_large", "message": "PDF 文件大小不能超过 5MB"},
        )

    # 2. 内存提取 PDF 文本并校验有效字数 (>= 50)
    try:
        extracted_text = extract_text_from_pdf_bytes(pdf_bytes)
    except FileTooLargeError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})
    except NoTextError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})
    except PdfInvalidError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})
    except ResumeError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})

    # 3. 姓名三来源融合与强脱敏
    user_row = conn.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    disp_name = user_row["display_name"] if user_row else None
    try:
        sanitized_text = sanitize_resume_text(extracted_text, self_name=body.self_name, display_name=disp_name)
    except SelfNameRequiredError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})

    existing_slot = get_resume_slot(conn, user_id, body.slot)
    # 画像生成失败时：同一份简历重传保留原画像；换了另一份简历就清空，
    # 免得新简历配着旧画像参与判断（画像为空的简历不发给大模型，点「重新生成画像」后补上）
    same_resume = bool(existing_slot) and existing_slot["resume_text"] == sanitized_text
    existing_profile = existing_slot["profile"] if same_resume else ""

    settings = request.app.state.settings

    # 4. 未配置 API Key 降级保存 (409 no_llm_key)
    if not settings.llm_api_key:
        save_resume_upload(
            conn,
            user_id=user_id,
            slot=body.slot,
            name=clean_name,
            resume_text=sanitized_text,
            profile=existing_profile,
        )
        return JSONResponse(
            status_code=409,
            content={
                "error": "no_llm_key",
                "message": "未配置 API Key（文字已保存，配置 Key 后可生成画像）",
                "slot": body.slot,
                "name": clean_name,
                "has_text": True,
                "text_chars": len(sanitized_text),
                "profile": existing_profile,
            },
        )

    # 5. 独立配额预占 (429 quota_exhausted)
    provider = urllib.parse.urlparse(settings.llm_base_url).hostname or "unknown"
    call_id = reserve(
        conn,
        user_id=user_id,
        provider=provider,
        model=settings.llm_model,
        purpose="resume_profile",
    )
    if call_id is None:
        save_resume_upload(
            conn,
            user_id=user_id,
            slot=body.slot,
            name=clean_name,
            resume_text=sanitized_text,
            profile=existing_profile,
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": "quota_exhausted",
                "message": "今日简历画像生成额度已用完（文字已保存，请明天重试生成或手动填写）",
                "slot": body.slot,
                "name": clean_name,
                "has_text": True,
                "text_chars": len(sanitized_text),
                "profile": existing_profile,
            },
        )

    # 6. 调用非思考模型生成画像
    transport = _llm_transport(request)

    try:
        profile_text, usage_dict = generate_resume_profile(
            settings,
            sanitized_text,
            transport=transport,
        )
    except NotSent as e:
        finish(conn, call_id, outcome="not_sent", settings=settings)
        save_resume_upload(
            conn,
            user_id=user_id,
            slot=body.slot,
            name=clean_name,
            resume_text=sanitized_text,
            profile=existing_profile,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": "llm_failed",
                "message": f"生成失败：{e}，可以稍后再试",
                "slot": body.slot,
                "name": clean_name,
                "has_text": True,
                "text_chars": len(sanitized_text),
                "profile": existing_profile,
            },
        )
    except Exception as e:
        finish(conn, call_id, outcome="failed", usage=getattr(e, "usage", None), settings=settings)
        save_resume_upload(
            conn,
            user_id=user_id,
            slot=body.slot,
            name=clean_name,
            resume_text=sanitized_text,
            profile=existing_profile,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": "llm_failed",
                "message": "生成失败：服务响应异常，可以稍后再试",
                "slot": body.slot,
                "name": clean_name,
                "has_text": True,
                "text_chars": len(sanitized_text),
                "profile": existing_profile,
            },
        )

    # 7. 结算并持久化更新
    finish(conn, call_id, outcome="ok", usage=usage_dict, settings=settings)
    save_resume_upload(
        conn,
        user_id=user_id,
        slot=body.slot,
        name=clean_name,
        resume_text=sanitized_text,
        profile=profile_text,
    )
    return {
        "slot": body.slot,
        "name": clean_name,
        "profile": profile_text,
        "text_chars": len(sanitized_text),
    }


@router.post("/v1/resumes/{slot}/regenerate")
def post_resume_regenerate(
    slot: int,
    body: ResumeRegeneratePayload | None = None,
    request: Request = None,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """利用已保存的 resume_text 重新脱敏并生成画像 (FR-011, T011)。"""
    if slot not in (1, 2, 3):
        return JSONResponse(
            status_code=422,
            content={"error": "slot_invalid", "message": "简历编号必须为 1、2 或 3"},
        )

    existing = get_resume_slot(conn, user_id, slot)
    if not existing or not existing["resume_text"]:
        return JSONResponse(
            status_code=404,
            content={"error": "resume_not_found", "message": "该简历槽位未上传过文字版简历，请先上传 PDF"},
        )

    user_row = conn.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    disp_name = user_row["display_name"] if user_row else None
    self_name = body.self_name if body else None

    try:
        sanitized_text = sanitize_resume_text(existing["resume_text"], self_name=self_name, display_name=disp_name)
    except SelfNameRequiredError as e:
        return JSONResponse(status_code=422, content={"error": e.error, "message": e.message})

    settings = request.app.state.settings
    if not settings.llm_api_key:
        return JSONResponse(
            status_code=409,
            content={"error": "no_llm_key", "message": "未配置 API Key（配置 Key 后可生成画像）"},
        )

    provider = urllib.parse.urlparse(settings.llm_base_url).hostname or "unknown"
    call_id = reserve(
        conn,
        user_id=user_id,
        provider=provider,
        model=settings.llm_model,
        purpose="resume_profile",
    )
    if call_id is None:
        return JSONResponse(
            status_code=429,
            content={"error": "quota_exhausted", "message": "今日简历画像生成额度已用完（请明天重试生成或手动填写）"},
        )

    transport = _llm_transport(request)

    try:
        profile_text, usage_dict = generate_resume_profile(
            settings,
            sanitized_text,
            transport=transport,
        )
    except NotSent as e:
        finish(conn, call_id, outcome="not_sent", settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": f"生成失败：{e}，可以稍后再试"},
        )
    except Exception as e:
        finish(conn, call_id, outcome="failed", usage=getattr(e, "usage", None), settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": "生成失败：服务响应异常，可以稍后再试"},
        )

    finish(conn, call_id, outcome="ok", usage=usage_dict, settings=settings)
    save_resume_upload(
        conn,
        user_id=user_id,
        slot=slot,
        name=existing["name"],
        resume_text=sanitized_text,
        profile=profile_text,
    )
    return {
        "slot": slot,
        "name": existing["name"],
        "profile": profile_text,
        "text_chars": len(sanitized_text),
    }


@router.get("/v1/resumes")
def get_resumes(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve all resume items for the paired user (FR-012)."""
    return list_resumes(conn, user_id)


@router.put("/v1/resumes")
def put_resumes(
    body: Any = Body(...),
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save and replace user's resume modifications (FR-013)."""
    try:
        count = save_resumes(conn, user_id, body)
    except ResumeError as e:
        return JSONResponse(
            status_code=422,
            content={"error": e.error, "message": e.message},
        )
    except ValueError as e:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": str(e)},
        )
    return {"ok": True, "count": count}


@router.delete("/v1/resumes/{slot}")
def delete_resume(
    slot: int,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """彻底物理删除指定 slot 的简历记录 (FR-014, T012)。"""
    if slot not in (1, 2, 3):
        return JSONResponse(
            status_code=422,
            content={"error": "slot_invalid", "message": "简历编号必须为 1、2 或 3"},
        )
    delete_resume_slot(conn, user_id, slot)
    return {"ok": True, "slot": slot}


class StrictIndustriesPayload(BaseModel):
    selected: list[str]


@router.get("/v1/strict-industries")
def get_strict_industries(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Retrieve available strict industries and user's selected strict industries (FR-010)."""
    available = read_strict_industries()
    selected = get_user_strict_industries(conn, user_id)
    return {"available": available, "selected": selected}


@router.put("/v1/strict-industries")
def put_strict_industries(
    body: StrictIndustriesPayload,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Save and replace user's selected strict industries (FR-010, FR-014)."""
    try:
        with transaction(conn, immediate=True):
            selected = save_user_strict_industries(conn, user_id, body.selected)
    except InvalidIndustryError as e:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_industry", "message": str(e)},
        )
    return {"ok": True, "selected": selected, "count": len(selected)}


class AssistPreviewPayload(BaseModel):
    encrypt_job_id: str
    job_title: str
    company_name: str
    location_name: str
    hr_name: str | None = None
    user_name: str | None = None
    messages: list[dict[str, Any]] = []
    force_mode: str | None = None


class AssistGeneratePayload(BaseModel):
    encrypt_job_id: str
    job_title: str
    company_name: str
    location_name: str
    hr_name: str | None = None
    user_name: str | None = None
    messages: list[dict[str, Any]] = []
    prompt_hash: str
    force_mode: str | None = None


def _assemble_assist(
    conn: sqlite3.Connection, user_id: str, body: AssistPreviewPayload | AssistGeneratePayload
) -> tuple[dict[str, Any], dict[str, Any]]:
    """预览与生成共用：取本机岗位的判断、HR 实际情况、经历素材和所在城市，组装发给大模型的请求。

    返回 (assembled, ctx)，ctx 里是 judgement、experiences、current_city。读不到姓名时抛 SelfNameUnavailable。
    """
    job = conn.execute(
        "SELECT id FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (body.encrypt_job_id,),
    ).fetchone()
    judgement = None
    hr_note = None
    if job:
        latest = latest_judgement(conn, user_id, job["id"])
        if latest and latest["status"] == "done" and latest["verdict"]:
            judgement = to_api(conn, latest)
        hr_note = hr_note_for(conn, user_id, job["id"])
    experiences = list_experiences(conn, user_id)
    prof = get_current_profile(conn, user_id)
    current_city = (prof.get("current_city") or "") if prof else ""
    assembled = assemble_assist_request(
        job_title=body.job_title,
        company_name=body.company_name,
        location_name=body.location_name,
        messages=body.messages,
        experiences=experiences,
        hr_name=body.hr_name,
        user_name=body.user_name,
        judgement=judgement,
        hr_note=hr_note,
        force_mode=body.force_mode,
        current_city=current_city,
    )
    return assembled, {"judgement": judgement, "experiences": experiences, "current_city": current_city}


@router.post("/v1/chat/preview")
def post_chat_preview(
    body: AssistPreviewPayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Preview sanitized chat data and prompt fingerprint before transmitting to LLM."""
    # 检查顺序: 我的姓名检查 (422 self_name_unavailable)
    if not body.user_name or not body.user_name.strip():
        return JSONResponse(
            status_code=422,
            content={"error": "self_name_unavailable", "message": "读不到你的姓名，暂时不能生成"},
        )

    has_consent = has_valid_consent(conn, user_id)
    try:
        assembled, _ctx = _assemble_assist(conn, user_id, body)
    except SelfNameUnavailable:
        return JSONResponse(
            status_code=422,
            content={"error": "self_name_unavailable", "message": "读不到你的姓名，暂时不能生成"},
        )

    return {**assist_preview_fields(assembled), "has_consent": has_consent}


@router.post("/v1/chat/generate")
def post_chat_generate(
    body: AssistGeneratePayload,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Generate targeted communication suggestions using DeepSeek completions."""
    # 检查顺序 1: 同意检查 (403 consent_required)
    if not has_valid_consent(conn, user_id):
        return JSONResponse(
            status_code=403,
            content={"error": "consent_required", "message": "尚未同意发送脱敏数据"},
        )

    # 检查顺序 2: 我的姓名检查 (422 self_name_unavailable)
    if not body.user_name or not body.user_name.strip():
        return JSONResponse(
            status_code=422,
            content={"error": "self_name_unavailable", "message": "读不到你的姓名，暂时不能生成"},
        )

    try:
        assembled, ctx = _assemble_assist(conn, user_id, body)
    except SelfNameUnavailable:
        return JSONResponse(
            status_code=422,
            content={"error": "self_name_unavailable", "message": "读不到你的姓名，暂时不能生成"},
        )

    # 检查顺序 3: 指纹比对 (400 hash_mismatch)
    if not body.prompt_hash or body.prompt_hash != assembled["prompt_hash"]:
        return JSONResponse(
            status_code=400,
            content={"error": "hash_mismatch", "message": "发送内容哈希不一致，已被安全拦截"},
        )

    settings = request.app.state.settings
    if not settings.llm_api_key:
        return JSONResponse(
            status_code=409,
            content={"error": "no_llm_key", "message": "请先在设置页填写 DeepSeek API Key"},
        )

    provider = urllib.parse.urlparse(settings.llm_base_url).hostname or "unknown"

    # 检查顺序 4: 预占生成次数 (429 quota_exhausted)
    call_id = reserve(
        conn,
        user_id=user_id,
        provider=provider,
        model=settings.llm_model,
        purpose="assist",
    )
    if call_id is None:
        return JSONResponse(
            status_code=429,
            content={"error": "quota_exhausted", "message": "今日生成次数已用完"},
        )

    transport = _llm_transport(request)

    try:
        result = generate_assist_suggestions(
            settings=settings,
            job_title=body.job_title,
            company_name=body.company_name,
            location_name=body.location_name,
            sanitized_messages=assembled["sanitized_messages"],
            experiences=ctx["experiences"],
            expected_hash=body.prompt_hash,
            judgement=ctx["judgement"],
            hr_note=assembled["hr_note"],
            force_mode=body.force_mode,
            transport=transport,
            current_city=ctx["current_city"],
            assembled=assembled,
        )
    except NotSent as e:
        finish(conn, call_id, outcome="not_sent", settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": f"生成失败：{e}，可以再点一次生成"},
        )
    except Timeout:
        finish(conn, call_id, outcome="timeout", settings=settings)
        return JSONResponse(
            status_code=504,
            content={"error": "llm_failed", "message": "生成失败：服务响应超时，可以再点一次生成"},
        )
    except (RetryableHttp, HttpError) as e:
        finish(conn, call_id, outcome="http_error", settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": f"生成失败：{e}，可以再点一次生成"},
        )
    except ParseError as e:
        finish(conn, call_id, outcome="parse_error", usage=getattr(e, "usage", None), settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": "生成失败：大模型响应解析失败，可以再点一次生成"},
        )
    except AllSuggestionsDroppedError as e:
        finish(conn, call_id, outcome="ok", usage=getattr(e, "usage", None), settings=settings)
        return JSONResponse(
            status_code=422,
            content={"error": "all_suggestions_dropped", "message": e.message},
        )
    except HashMismatchError as e:
        finish(conn, call_id, outcome="not_sent", settings=settings)
        return JSONResponse(
            status_code=400,
            content={"error": "hash_mismatch", "message": e.message},
        )
    except Exception as e:
        finish(conn, call_id, outcome="failed", usage=getattr(e, "usage", None), settings=settings)
        return JSONResponse(
            status_code=502,
            content={"error": "llm_failed", "message": "生成失败：服务响应异常，可以再点一次生成"},
        )

    finish(conn, call_id, outcome="ok", usage=result.get("usage"), settings=settings)
    quota_remaining = remaining_today(conn, user_id, purpose="assist")

    return {
        "company_name": result["company_name"],
        "job_title": result["job_title"],
        "mode": result["mode"],
        "mode_label": result["mode_label"],
        "mode_basis": result["mode_basis"],
        "has_jet_judgement": result["has_jet_judgement"],
        "message_count": result["message_count"],
        "suggestions": result["suggestions"],
        "experience_note": result["experience_note"],
        "questions": result["questions"],
        "quota_remaining": quota_remaining,
        "unanswered_facts": result.get("unanswered_facts", []),
        "request_notice": result.get("request_notice"),
        "dropped_summary": result.get("dropped_summary", []),
    }


def _prejudge_result(
    conn: sqlite3.Connection, user_id: str, status: str, prejudgements: dict[str, Any]
) -> dict[str, Any]:
    """列表预判接口的统一返回：状态、预判结果和当天的预判用量。"""
    usage = usage_today(conn, user_id, purpose="prejudge")
    return {
        "status": status,
        "prejudgements": prejudgements,
        "usage": {"used": usage["used"], "limit": usage["limit"], "remaining": usage["remaining"]},
    }


@router.post("/v1/prejudge")
def post_prejudge(
    body: PrejudgeRequest,
    request: Request,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """
    Perform batch prejudgement on list jobs (T007).
    """
    # 1. 检查求职画像 (no_profile)
    profile = get_current_profile(conn, user_id)
    if profile is None:
        return _prejudge_result(conn, user_id, "no_profile", {})

    # 2. 仅处理数据库中已存在的岗位
    pids = [j.platform_job_id for j in body.jobs if j.platform_job_id]
    if not pids:
        return _prejudge_result(conn, user_id, "empty", {})

    placeholders = ",".join("?" for _ in pids)
    db_jobs = conn.execute(
        f"SELECT id, platform_job_id FROM jobs WHERE platform = 'boss' AND platform_job_id IN ({placeholders})",
        pids,
    ).fetchall()
    job_id_by_pid = {r["platform_job_id"]: r["id"] for r in db_jobs}

    # 3. 过滤排除已有该用户有效正式判断（status='done' 且有结论）的岗位
    prejudgements_out: dict[str, Any] = {}
    unjudged_jobs: list[PrejudgeJobItem] = []
    for j in body.jobs:
        job_id = job_id_by_pid.get(j.platform_job_id)
        if not job_id:
            continue
        done_row = conn.execute(
            "SELECT 1 FROM judgements WHERE user_id = ? AND job_id = ? AND status = 'done' AND verdict IS NOT NULL",
            (user_id, job_id),
        ).fetchone()
        if done_row:
            continue
        unjudged_jobs.append(j)

    if not unjudged_jobs:
        return _prejudge_result(conn, user_id, "empty", {})

    # 4. 对当前有效画像已有预判记录的岗位，直接从 prejudgements 缓存提取
    candidate_jobs: list[PrejudgeJobItem] = []
    for j in unjudged_jobs:
        job_id = job_id_by_pid[j.platform_job_id]
        cached = conn.execute(
            "SELECT level, reason, created_at FROM prejudgements WHERE user_id = ? AND job_id = ? AND profile_id = ?",
            (user_id, job_id, profile["id"]),
        ).fetchone()
        if cached:
            prejudgements_out[j.platform_job_id] = {
                "level": cached["level"],
                "reason": cached["reason"],
                "created_at": cached["created_at"],
            }
        else:
            candidate_jobs.append(j)

    # 若候选岗位为空，直接返回已命中缓存
    if not candidate_jobs:
        return _prejudge_result(conn, user_id, "ok" if prejudgements_out else "empty", prejudgements_out)

    # 5. 检查是否配置 API Key (no_llm_key)
    settings = request.app.state.settings
    if not settings.llm_api_key:
        return _prejudge_result(conn, user_id, "no_llm_key", prejudgements_out)

    # 6. 独立配额预占 (quota_exhausted)
    provider = urllib.parse.urlparse(settings.llm_base_url).hostname or "unknown"
    engine = settings.judge_engine
    call_id = reserve(
        conn,
        user_id=user_id,
        provider=provider,
        model=settings.llm_model,
        purpose="prejudge",
    )
    if call_id is None:
        return _prejudge_result(conn, user_id, "quota_exhausted", prejudgements_out)

    # 7. 候选岗位打包为一次非思考模型调用
    strict_industries = get_user_strict_industries(conn, user_id)
    batch_jobs = candidate_jobs[:40]
    messages = build_prejudge_messages(
        profile,
        [j.model_dump() for j in batch_jobs],
        strict_industries=strict_industries,
    )

    transport = _llm_transport(request)

    thinking = ("think" in engine and "no-think" not in engine)
    try:
        content, usage_dict = call_once(
            settings,
            messages,
            transport=transport,
            thinking=thinking,
            max_tokens=3000,
        )
    except NotSent:
        finish(conn, call_id, outcome="not_sent", settings=settings)
        return _prejudge_result(conn, user_id, "failed", prejudgements_out)
    except Exception as e:
        finish(conn, call_id, outcome="failed", usage=getattr(e, "usage", None), settings=settings)
        return _prejudge_result(conn, user_id, "failed", prejudgements_out)

    # 8. 校验与入库
    try:
        seq_to_job = {str(idx): j for idx, j in enumerate(batch_jobs, 1)}
        parsed = parse_prejudge_response(content, list(seq_to_job.keys()))
        now_iso = utc_now()
        new_out: dict[str, dict[str, Any]] = {}

        with transaction(conn):
            for seq_id, item_res in parsed.items():
                job = seq_to_job.get(seq_id)
                if not job:
                    continue
                pid = job.platform_job_id
                job_id = job_id_by_pid.get(pid)
                if job_id:
                    conn.execute(
                        "INSERT OR REPLACE INTO prejudgements "
                        "(user_id, job_id, level, reason, profile_id, engine, llm_call_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            user_id,
                            job_id,
                            item_res["level"],
                            item_res["reason"],
                            profile["id"],
                            engine,
                            call_id,
                            now_iso,
                        ),
                    )
                    new_out[pid] = {
                        "level": item_res["level"],
                        "reason": item_res["reason"],
                        "created_at": now_iso,
                    }
        # 入库成功后才放进返回结果，避免回滚后仍返回没存进去的预判
        prejudgements_out.update(new_out)
    except Exception:
        # 解析或入库出错：把这次调用记为失败（不再停在 reserved），按失败返回
        finish(conn, call_id, outcome="failed", usage=usage_dict, settings=settings)
        return _prejudge_result(conn, user_id, "failed", prejudgements_out)

    finish(conn, call_id, outcome="ok", usage=usage_dict, settings=settings)
    return _prejudge_result(conn, user_id, "ok", prejudgements_out)


@router.get("/v1/prejudge/settings")
def get_prejudge_settings(
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Get prejudge daily limit and today's usage (T008)."""
    usage = usage_today(conn, user_id, purpose="prejudge")
    return {
        "daily_prejudge_limit": usage["limit"],
        "used_today": usage["used"],
        "remaining_today": usage["remaining"],
    }


@router.put("/v1/prejudge/settings")
def put_prejudge_settings(
    body: PrejudgeSettingsPayload,
    user_id: str = Depends(require_paired),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Update prejudge daily limit (T008)."""
    with transaction(conn):
        conn.execute(
            "UPDATE user_settings SET daily_prejudge_limit = ? WHERE user_id = ?",
            (body.daily_prejudge_limit, user_id),
        )
    usage = usage_today(conn, user_id, purpose="prejudge")
    return {
        "ok": True,
        "daily_prejudge_limit": body.daily_prejudge_limit,
        "used_today": usage["used"],
        "remaining_today": usage["remaining"],
    }
