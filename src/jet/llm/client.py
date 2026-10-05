# HTTP/重试骨架参考 jet-demo d13aa43 job_analysis/providers/deepseek.py，重写
from dataclasses import dataclass
import sqlite3
import time
from typing import Any, Mapping, Sequence
import urllib.parse
import httpx

from jet.config import Settings
from jet.domain.industry import (
    get_user_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
)
from jet.domain.quotes import annotate_facts
from jet.domain.resume import list_resumes
from jet.llm.prompt import ParseError
from jet.llm.quota import finish, reserve
from jet.llm.versions import get_prompt_version


class NotSent(Exception):
    """Raised when request was definitely not sent out (e.g. connection refused)."""
    pass


class Timeout(Exception):
    """Raised when request timed out waiting for server."""
    pass


class RetryableHttp(Exception):
    """Raised for transient server HTTP errors (429 or 5xx) that should be retried."""
    pass


class HttpError(Exception):
    """Raised for non-transient HTTP errors (e.g. 400, 401, 403, 404)."""
    pass


@dataclass
class LlmOutcome:
    status: str  # "done", "failed", "quota_exhausted"
    verdict: str | None = None
    reasons: list[str] | None = None
    facts: dict[str, Any] | None = None
    derivation: list[str] | None = None
    verdict_reason: str | None = None
    hr_questions: list[str] | None = None
    resume_suggestion: dict[str, Any] | None = None
    prompt_version: str | None = None
    engine: str | None = None
    usage: dict[str, Any] | None = None
    cost_cny: float | None = None
    error: str | None = None


def call_once(
    settings: Settings,
    messages: Sequence[Mapping[str, str]],
    transport: httpx.BaseTransport | None = None,
    *,
    thinking: bool = False,
    max_tokens: int = 400,
) -> tuple[str, dict[str, Any]]:
    """
    Perform a single HTTP call to the OpenAI-compatible completions endpoint.

    Returns (content, usage_dict).
    """
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key or ''}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": list(messages),
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled" if thinking else "disabled"},
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    timeout_s = settings.llm_thinking_timeout_s if thinking else settings.llm_timeout_s

    try:
        with httpx.Client(transport=transport, timeout=timeout_s) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as e:
        raise NotSent(f"连接大模型服务失败: {e}") from e
    except httpx.ConnectTimeout as e:
        raise NotSent(f"连接大模型服务超时（连接超时）: {e}") from e
    except httpx.TimeoutException as e:
        raise Timeout(f"大模型调用超时: {e}") from e
    except httpx.RequestError as e:
        # 请求可能已发出（例如读响应时断开）：无法确认是否计费，按已计费处理（data-model 额度扣减规则）
        raise RetryableHttp(f"网络请求失败: {e}") from e

    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableHttp(f"HTTP {resp.status_code}: {resp.text[:200]}")
    if resp.status_code != 200:
        raise HttpError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception as e:
        raise ParseError(f"响应不是合法 JSON: {e}") from e

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise ParseError("响应缺失 choices 列表")
    message = choices[0].get("message")
    if not message or not isinstance(message, dict):
        raise ParseError("响应缺失 choices[0].message")
    content = message.get("content")
    usage = data.get("usage") or {}
    if content is None or not str(content).strip():
        # 开启思考时，思考过程也计入 max_tokens；上限太小会导致正式答案为空（finish_reason=length）
        finish_reason = choices[0].get("finish_reason")
        hint = "，输出被截断（思考过程用完了输出上限）" if finish_reason == "length" else ""
        err = ParseError(f"大模型返回内容为空（finish_reason={finish_reason}{hint}）")
        err.usage = usage  # 仍然计费：记录用量
        raise err

    return content, usage


def run_llm_judgement(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    user_id: str,
    judgement_id: int | None = None,
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    transport: httpx.BaseTransport | None = None,
    backoff_delays: Sequence[float] = (0.5, 1.0),
    prompt_version: str | None = None,
    engine: str | None = None,
    purpose: str = "judge",
    eval_run_id: str | None = None,
    eval_limit: int | None = None,
    known_facts: Mapping[str, Any] | None = None,
    truncate_derivation: bool = False,
) -> LlmOutcome:
    """
    Execute an LLM judgement with retries, quota reservations, and error recording.
    """
    p_ver = prompt_version or settings.prompt_version
    version = get_prompt_version(p_ver)
    eng = engine or settings.judge_engine
    thinking = ("think" in eng and "no-think" not in eng)
    # 开启思考时思考过程也占 max_tokens（2026-09-25 评测：1200 时组合 C 全部返回空内容）
    if thinking:
        max_tokens = 8192
    else:
        max_tokens = version.max_tokens

    if not settings.llm_api_key:
        return LlmOutcome(
            status="failed",
            error="未配置 API Key（请在设置页填写）",
            prompt_version=p_ver,
            engine=eng,
        )

    provider = urllib.parse.urlparse(settings.llm_base_url).hostname or "unknown"
    resumes: list[dict[str, Any]] = []
    if version.needs_resumes:
        resumes = list_resumes(conn, user_id)

    user_strict_industries: list[str] = []
    user_aliases: dict[str, Any] = {}
    user_keywords: dict[str, Any] = {}
    if version.needs_strict_industries:
        user_strict_industries = get_user_strict_industries(conn, user_id)
        if user_strict_industries:
            all_aliases = read_strict_industry_aliases()
            all_keywords = read_strict_industry_keywords()
            user_aliases = {k: all_aliases[k] for k in user_strict_industries if k in all_aliases}
            user_keywords = {k: all_keywords[k] for k in user_strict_industries if k in all_keywords}

    msg_kwargs: dict[str, Any] = {}
    if version.needs_known_facts:
        msg_kwargs["known_facts"] = known_facts
    if version.needs_strict_industries:
        msg_kwargs["strict_industries"] = user_strict_industries
        msg_kwargs["strict_industry_aliases"] = user_aliases
        msg_kwargs["strict_industry_keywords"] = user_keywords
    if version.needs_resumes:
        msg_kwargs["resumes"] = resumes

    messages = version.module.build_messages(profile, job_version, **msg_kwargs)

    max_attempts = 3
    delays = list(backoff_delays)

    for attempt in range(max_attempts):
        call_id = reserve(
            conn,
            user_id=user_id,
            judgement_id=judgement_id,
            provider=provider,
            model=settings.llm_model,
            purpose=purpose,
            eval_run_id=eval_run_id,
            limit_override=eval_limit,
        )
        if call_id is None:
            if attempt == 0:
                err = "已达评测上限" if purpose == "eval" else "今日判断额度已用完，明天 0 点恢复"
                return LlmOutcome(status="quota_exhausted", error=err, prompt_version=p_ver, engine=eng)
            return LlmOutcome(status="failed", error="重试时额度已用完", prompt_version=p_ver, engine=eng)

        try:
            content, usage = call_once(
                settings,
                messages,
                transport=transport,
                thinking=thinking,
                max_tokens=max_tokens,
            )
        except NotSent as e:
            finish(conn, call_id, outcome="not_sent", settings=settings)
            if attempt < max_attempts - 1:
                delay = delays[attempt] if attempt < len(delays) else 1.0
                if delay > 0:
                    time.sleep(delay)
                continue
            return LlmOutcome(status="failed", error=str(e), prompt_version=p_ver, engine=eng)
        except Timeout as e:
            finish(conn, call_id, outcome="timeout", settings=settings)
            if attempt < max_attempts - 1:
                delay = delays[attempt] if attempt < len(delays) else 1.0
                if delay > 0:
                    time.sleep(delay)
                continue
            return LlmOutcome(status="failed", error=str(e), prompt_version=p_ver, engine=eng)
        except RetryableHttp as e:
            finish(conn, call_id, outcome="http_error", settings=settings)
            if attempt < max_attempts - 1:
                delay = delays[attempt] if attempt < len(delays) else 1.0
                if delay > 0:
                    time.sleep(delay)
                continue
            return LlmOutcome(status="failed", error=str(e), prompt_version=p_ver, engine=eng)
        except HttpError as e:
            finish(conn, call_id, outcome="http_error", settings=settings)
            return LlmOutcome(status="failed", error=str(e), prompt_version=p_ver, engine=eng)
        except ParseError as e:
            finish(conn, call_id, outcome="parse_error", usage=getattr(e, "usage", None), settings=settings)
            return LlmOutcome(status="failed", error=str(e), prompt_version=p_ver, engine=eng)

        # Successful HTTP response: parse based on prompt version table
        try:
            parse_kwargs: dict[str, Any] = {}
            if version.supports_truncate_derivation:
                parse_kwargs["truncate_derivation"] = truncate_derivation
            if version.has_resume_suggestion:
                valid_slots = {r["slot"] for r in resumes} if len(resumes) >= 2 else set()
                parse_kwargs["valid_slots"] = valid_slots
            if version.parse_needs_profile:
                parse_kwargs["profile"] = profile

            parsed = version.parse_func(content, **parse_kwargs)

            if not version.has_facts_and_derivation:
                verdict, reasons = parsed
                facts = None
                derivation = None
                verdict_reason = None
                hr_questions = None
                resume_suggestion = None
            else:
                reasons = None
                facts = parsed[0]
                verdict = parsed[1]
                derivation = parsed[2]
                idx = 3
                if version.has_verdict_reason:
                    verdict_reason = parsed[idx]
                    idx += 1
                else:
                    verdict_reason = None
                if version.has_hr_questions:
                    hr_questions = parsed[idx]
                    idx += 1
                else:
                    hr_questions = None
                if version.has_resume_suggestion:
                    resume_suggestion = parsed[idx]
                    idx += 1
                else:
                    resume_suggestion = None

            if version.apply_city_salary_cap:
                verdict, derivation = version.module.apply_city_salary_cap(verdict, derivation, profile, job_version)

            if version.annotate_facts and facts is not None:
                desc = str(job_version.get("description") or "")
                facts = annotate_facts(facts, desc)

            cost = finish(conn, call_id, outcome="ok", usage=usage, settings=settings)
            return LlmOutcome(
                status="done",
                verdict=verdict,
                reasons=reasons,
                facts=facts,
                derivation=derivation,
                verdict_reason=verdict_reason,
                hr_questions=hr_questions,
                resume_suggestion=resume_suggestion,
                prompt_version=p_ver,
                engine=eng,
                usage=usage,
                cost_cny=cost,
            )
        except ParseError as e:
            finish(conn, call_id, outcome="parse_error", usage=usage, settings=settings)
            return LlmOutcome(
                status="failed",
                error=f"大模型返回无法解析: {e}",
                prompt_version=p_ver,
                engine=eng,
                usage=usage,
            )
        except Exception:
            # 后处理出了意外错误：先把这次调用记为失败（不再停在 reserved），再照常抛给 worker 兜底
            finish(conn, call_id, outcome="failed", usage=usage, settings=settings)
            raise

    return LlmOutcome(status="failed", error="超过最大重试次数", prompt_version=p_ver, engine=eng)
