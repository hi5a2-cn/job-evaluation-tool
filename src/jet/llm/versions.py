"""提示词版本表 (Prompt Version Table)

登记所有提示词版本及其能力，提供按版本名查询与全量版本列表功能。
"""
from dataclasses import dataclass
from typing import Any, Callable

from jet.llm import prompt as prompt_v1
from jet.llm import prompt_v2
from jet.llm import prompt_v3
from jet.llm import prompt_v4
from jet.llm import prompt_v5
from jet.llm import prompt_v6
from jet.llm import prompt_v7
from jet.llm import prompt_v8
from jet.llm import prompt_v9
from jet.llm import prompt_v10


class UnknownPromptVersionError(ValueError):
    """Raised when an unknown prompt version is requested."""
    pass


@dataclass(frozen=True)
class PromptVersion:
    name: str
    module: Any
    parse_func: Callable[..., Any]
    needs_strict_industries: bool
    needs_resumes: bool
    has_facts_and_derivation: bool
    has_verdict_reason: bool
    has_hr_questions: bool
    has_resume_suggestion: bool
    apply_city_salary_cap: bool
    supports_truncate_derivation: bool
    annotate_facts: bool
    max_tokens: int
    supports_review: bool
    needs_known_facts: bool = True
    parse_needs_profile: bool = False


DEFAULT_PROMPT_VERSION = "v10"
STALE_METHOD_BASELINE = "v9"
EVAL_BASELINE_PROMPT_VERSION = "v1"
EARLIEST_PROMPT_VERSION = "v1"

_PROMPT_VERSIONS: dict[str, PromptVersion] = {
    "v1": PromptVersion(
        name="v1",
        module=prompt_v1,
        parse_func=prompt_v1.parse_verdict,
        needs_strict_industries=False,
        needs_resumes=False,
        has_facts_and_derivation=False,
        has_verdict_reason=False,
        has_hr_questions=False,
        has_resume_suggestion=False,
        apply_city_salary_cap=False,
        supports_truncate_derivation=False,
        annotate_facts=False,
        max_tokens=400,
        supports_review=False,
        needs_known_facts=False,
    ),
    "v2": PromptVersion(
        name="v2",
        module=prompt_v2,
        parse_func=prompt_v2.parse_facts,
        needs_strict_industries=False,
        needs_resumes=False,
        has_facts_and_derivation=True,
        has_verdict_reason=False,
        has_hr_questions=False,
        has_resume_suggestion=False,
        apply_city_salary_cap=False,
        supports_truncate_derivation=False,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=False,
        needs_known_facts=True,
    ),
    "v3": PromptVersion(
        name="v3",
        module=prompt_v3,
        parse_func=prompt_v3.parse_facts,
        needs_strict_industries=False,
        needs_resumes=False,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=False,
        has_resume_suggestion=False,
        apply_city_salary_cap=False,
        supports_truncate_derivation=False,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=False,
        needs_known_facts=True,
    ),
    "v4": PromptVersion(
        name="v4",
        module=prompt_v4,
        parse_func=prompt_v4.parse_facts,
        needs_strict_industries=False,
        needs_resumes=False,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=False,
        apply_city_salary_cap=False,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v5": PromptVersion(
        name="v5",
        module=prompt_v5,
        parse_func=prompt_v5.parse_facts,
        needs_strict_industries=True,
        needs_resumes=False,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=False,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v6": PromptVersion(
        name="v6",
        module=prompt_v6,
        parse_func=prompt_v6.parse_facts,
        needs_strict_industries=True,
        needs_resumes=True,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=True,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v7": PromptVersion(
        name="v7",
        module=prompt_v7,
        parse_func=prompt_v7.parse_facts,
        needs_strict_industries=True,
        needs_resumes=True,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=True,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v8": PromptVersion(
        name="v8",
        module=prompt_v8,
        parse_func=prompt_v8.parse_facts,
        needs_strict_industries=True,
        needs_resumes=True,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=True,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v9": PromptVersion(
        name="v9",
        module=prompt_v9,
        parse_func=prompt_v9.parse_facts,
        needs_strict_industries=True,
        needs_resumes=True,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=True,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
    ),
    "v10": PromptVersion(
        name="v10",
        module=prompt_v10,
        parse_func=prompt_v10.parse_facts,
        needs_strict_industries=True,
        needs_resumes=True,
        has_facts_and_derivation=True,
        has_verdict_reason=True,
        has_hr_questions=True,
        has_resume_suggestion=True,
        apply_city_salary_cap=True,
        supports_truncate_derivation=True,
        annotate_facts=True,
        max_tokens=1200,
        supports_review=True,
        needs_known_facts=True,
        parse_needs_profile=True,
    ),
}


def list_prompt_versions() -> list[str]:
    """返回全部可用版本名列表，按版本升序排列。"""
    def _v_key(k: str) -> int:
        if k.startswith("v") and k[1:].isdigit():
            return int(k[1:])
        return 999999
    return sorted(_PROMPT_VERSIONS.keys(), key=_v_key)


def get_prompt_version(name: str) -> PromptVersion:
    """按版本名取版本项，版本名不在表里时抛出 UnknownPromptVersionError，消息列出所有可用版本。"""
    version = _PROMPT_VERSIONS.get(name)
    if version is None:
        valid = ", ".join(list_prompt_versions())
        raise UnknownPromptVersionError(f"未知的提示词版本: '{name}'，当前可用版本: {valid}")
    return version
