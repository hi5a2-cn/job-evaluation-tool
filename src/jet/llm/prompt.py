import json
import re
from typing import Any, Mapping

PROMPT_VERSION = "v1"


SYSTEM_PROMPT = """你是一个专业的求职匹配评估专家。请根据候选人的求职画像和岗位详情，客观判断该岗位是否适合候选人。
你必须且只能以 JSON 对象格式输出评估结论，格式如下：
{
  "verdict": "fit" 或 "unfit",
  "reasons": ["理由1", "理由2", "理由3"]
}
约束：
1. verdict 必须是 "fit"（适合）或 "unfit"（不适合）之一。
2. reasons 为结论理由列表，最多 3 条，每条理由不超过 40 个汉字，必须使用中文。
3. 严格只输出合法 JSON，不要包含任何 markdown 代码块标记或其他额外文本。
"""


class ParseError(Exception):
    """Raised when LLM response cannot be strictly parsed into expected verdict structure."""
    pass


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Build messages array with system prompt first, followed by profile and job details."""
    # 调用方可能传入 sqlite3.Row（没有 .get），统一转成 dict
    profile = dict(profile)
    job_version = dict(job_version)
    directions = "、".join(profile.get("directions") or [])
    raw_keywords = profile.get("keywords") or []
    keywords = "、".join(raw_keywords) if raw_keywords else "无"
    cities = "、".join(profile.get("cities") or [])
    min_k = f"{profile.get('min_monthly_k')}K" if profile.get("min_monthly_k") is not None else "不限"
    raw_exclude = profile.get("exclude_keywords") or []
    exclude_keywords = "、".join(raw_exclude) if raw_exclude else "无"

    title = str(job_version.get("title") or "")
    if bool(job_version.get("salary_visible")) and job_version.get("salary_raw"):
        salary = str(job_version.get("salary_raw"))
    else:
        salary = "薪资不可见"

    city = str(job_version.get("city") or "")
    district = str(job_version.get("district") or "未提供")
    description = str(job_version.get("description") or "无")

    user_content = f"""【候选人画像】
- 目标方向：{directions}
- 技能与关键词：{keywords}
- 期望城市：{cities}
- 最低月薪底线：{min_k}
- 不接受条件：{exclude_keywords}

【岗位信息】
- 职位名称：{title}
- 薪资范围：{salary}
- 工作城市：{city}
- 所属区域：{district}
- 职位描述：
{description}

请依据上述画像和岗位信息给出评估结果，只返回符合要求的 JSON。"""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_verdict(text: str) -> tuple[str, list[str]]:
    """
    Parse and validate LLM output into (verdict, reasons).

    Enforces:
    - Strict JSON
    - verdict in ('fit', 'unfit')
    - reasons as non-empty list of non-empty strings
    - Truncates reasons to at most 3 items, each at most 40 characters.
    Raises ParseError on any validation violation.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ParseError("大模型返回内容为空")

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ParseError(f"大模型返回无法解析为 JSON: {e}") from e

    if not isinstance(data, dict):
        raise ParseError("返回内容不是 JSON 对象")

    verdict = data.get("verdict")
    if verdict not in ("fit", "unfit"):
        raise ParseError(f"verdict 必须是 'fit' 或 'unfit'，收到: {verdict}")

    reasons = data.get("reasons")
    if not isinstance(reasons, list) or len(reasons) == 0:
        raise ParseError("reasons 必须是非空列表")

    parsed_reasons: list[str] = []
    for r in reasons:
        if not isinstance(r, str) or not r.strip():
            raise ParseError("reasons 中包含非字符串或空白项")
        # Truncate each reason to max 40 characters
        parsed_reasons.append(r.strip()[:40])

    # Truncate reasons to max 3 items
    return verdict, parsed_reasons[:3]
