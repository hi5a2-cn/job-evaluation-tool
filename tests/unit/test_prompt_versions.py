"""单元测试：提示词版本表与配置校验 (014-prompt-version-table T006)。"""
from pathlib import Path
import pytest

from jet.config import load_settings
from jet.llm.versions import (
    DEFAULT_PROMPT_VERSION,
    EARLIEST_PROMPT_VERSION,
    EVAL_BASELINE_PROMPT_VERSION,
    STALE_METHOD_BASELINE,
    UnknownPromptVersionError,
    get_prompt_version,
    list_prompt_versions,
)


def test_versions_in_table():
    versions = list_prompt_versions()
    expected_versions = [f"v{i}" for i in range(1, 11)]
    assert versions == expected_versions

    for v in expected_versions:
        item = get_prompt_version(v)
        assert item.name == v
        assert item.module is not None
        assert callable(item.parse_func)

    # v1 特征
    v1 = get_prompt_version("v1")
    assert v1.max_tokens == 400
    assert not v1.supports_review
    assert not v1.has_facts_and_derivation
    assert not v1.needs_known_facts

    # v2 特征
    v2 = get_prompt_version("v2")
    assert v2.max_tokens == 1200
    assert not v2.supports_review
    assert v2.has_facts_and_derivation
    assert not v2.has_verdict_reason

    # v3 特征
    v3 = get_prompt_version("v3")
    assert v3.has_verdict_reason
    assert not v3.has_hr_questions

    # v4 特征
    v4 = get_prompt_version("v4")
    assert v4.supports_review
    assert v4.has_hr_questions
    assert not v4.apply_city_salary_cap

    # v5 特征
    v5 = get_prompt_version("v5")
    assert v5.supports_review
    assert v5.apply_city_salary_cap
    assert v5.needs_strict_industries
    assert not v5.needs_resumes

    # v6-v10 特征
    for v_name in ("v6", "v7", "v8", "v9", "v10"):
        v_item = get_prompt_version(v_name)
        assert v_item.supports_review
        assert v_item.apply_city_salary_cap
        assert v_item.needs_strict_industries
        assert v_item.needs_resumes
        assert v_item.has_resume_suggestion

    # v10 特征
    v10 = get_prompt_version("v10")
    assert v10.parse_needs_profile
    assert not get_prompt_version("v9").parse_needs_profile


def test_default_version_and_baseline_in_table():
    available = set(list_prompt_versions())
    assert DEFAULT_PROMPT_VERSION in available
    assert STALE_METHOD_BASELINE in available
    assert EVAL_BASELINE_PROMPT_VERSION in available
    assert EARLIEST_PROMPT_VERSION in available

    assert DEFAULT_PROMPT_VERSION == "v10"
    assert STALE_METHOD_BASELINE == "v9"
    assert EVAL_BASELINE_PROMPT_VERSION == "v1"
    assert EARLIEST_PROMPT_VERSION == "v1"


def test_unknown_version_query_raises():
    with pytest.raises(UnknownPromptVersionError) as exc_info:
        get_prompt_version("v99")
    err_msg = str(exc_info.value)
    assert "v99" in err_msg
    for v in list_prompt_versions():
        assert v in err_msg


def test_load_settings_invalid_prompt_version(tmp_path: Path):
    with pytest.raises(ValueError) as exc_info:
        load_settings(data_dir=tmp_path, env={"JET_PROMPT_VERSION": "v99"})
    err_msg = str(exc_info.value)
    assert "JET_PROMPT_VERSION" in err_msg
    assert "v99" in err_msg
    for v in list_prompt_versions():
        assert v in err_msg


def test_load_settings_valid_prompt_version(tmp_path: Path):
    settings = load_settings(data_dir=tmp_path, env={"JET_PROMPT_VERSION": "v5"})
    assert settings.prompt_version == "v5"

    default_settings = load_settings(data_dir=tmp_path, env={})
    assert default_settings.prompt_version == DEFAULT_PROMPT_VERSION


def test_no_prompt_version_literals_in_core_modules():
    """SC-003: client.py, worker.py, routes.py, judgements.py, job_status.py 中不得含有版本号字面量。"""
    import re

    core_files = [
        "src/jet/llm/client.py",
        "src/jet/worker.py",
        "src/jet/api/routes.py",
        "src/jet/domain/judgements.py",
        "src/jet/domain/job_status.py",
    ]
    pattern = re.compile(r"""['"]v\d+['"]""")
    matches = {}
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in core_files:
        path = repo_root / rel_path
        content = path.read_text(encoding="utf-8")
        found = [line.strip() for line in content.splitlines() if pattern.search(line)]
        if found:
            matches[rel_path] = found

    assert not matches, f"Found version literals: {matches}"
