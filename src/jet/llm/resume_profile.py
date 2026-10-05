"""简历画像 (resume_profile) 提炼提示词与大模型调用模块 (009-resume-pdf, T010)。

根据本机脱敏后的简历正文，调用非思考引擎提炼适合方向与核心亮点（<=300字）。
安全底线：发给大模型的只有脱敏后的简历正文 + 提炼指令；严禁发送简历名称、文件名或用户信息。
日志中严禁打印完整的 Prompt 或模型输出全文。
"""

import json
import re
from typing import Any
import httpx

from jet.config import Settings
from jet.llm.client import ParseError, call_once
from jet.logging import get_logger

logger = get_logger(__name__)

MAX_RESUME_TEXT_CHARS: int = 8000

SYSTEM_PROMPT = """你是一位资深求职顾问。请根据候选人脱敏后的简历正文，提炼一份精炼的"简历画像"。

生成要求：
1. 结构包括两部分：
   - 适合方向：一句话概括最适合该候选人的岗位方向与业务领域；
   - 亮点：3～5 条核心亮点（如核心能力、代表性成果与量化结果、行业/领域经验、工具或方法）。
2. 仅依据简历中提及的事实提炼，严禁编造或推测未提及的经历。
3. 严格只输出合法 JSON，不要包含任何 markdown 代码块或解释文字。

输出 JSON 格式规范：
{
  "direction": "适合的岗位方向，一句话",
  "highlights": [
    "亮点1",
    "亮点2",
    "亮点3"
  ]
}"""


def build_profile_messages(sanitized_text: str) -> list[dict[str, str]]:
    """构建提炼简历画像的消息列表。
    只包含脱敏后的正文（最多 8000 字，超出截断）与提炼指令，绝不包含简历名称、文件名、用户标识。
    """
    truncated_text = (
        sanitized_text[:MAX_RESUME_TEXT_CHARS]
        if len(sanitized_text) > MAX_RESUME_TEXT_CHARS
        else sanitized_text
    )
    user_content = (
        f"以下是候选人脱敏后的简历正文：\n\n"
        f"{truncated_text}\n\n"
        f"请根据上述正文提炼简历画像。请严格只输出符合要求的 JSON。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_profile_response(content: str | None) -> tuple[str, list[str]]:
    """解析大模型返回的简历画像 JSON。

    去掉可能的 ``` 包裹后 json.loads。
    direction 非空、highlights 为 3～5 个非空字符串（多于 5 条取前 5 条；少于 3 条或解析失败抛 ParseError）。
    """
    cleaned = (content or "").strip()
    if not cleaned:
        raise ParseError("大模型返回内容为空")

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    elif "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ParseError(f"大模型返回无法解析为 JSON: {e}") from e

    if not isinstance(data, dict):
        raise ParseError("大模型返回内容不是 JSON 对象")

    direction = data.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        raise ParseError("direction 缺失或为空")
    direction = direction.strip()

    raw_highlights = data.get("highlights")
    if not isinstance(raw_highlights, list):
        raise ParseError("highlights 必须是列表")

    valid_highlights: list[str] = []
    for item in raw_highlights:
        if not isinstance(item, str) or not item.strip():
            raise ParseError("highlights 中包含非字符串或空白项")
        valid_highlights.append(item.strip())

    if len(valid_highlights) < 3:
        raise ParseError(f"highlights 少于 3 条（实际 {len(valid_highlights)} 条）")

    return direction, valid_highlights[:5]


def format_profile_text(direction: str, highlights: list[str]) -> str:
    """在服务端拼成画像文本：
    适合方向：{direction}
    亮点：
    1. …
    2. …
    总长超过 300 字时，按条目从后往前删除亮点（至少保留 3 条），仍超则截断到 300 字。
    """
    items = list(highlights)

    def _render(hl_list: list[str]) -> str:
        lines = [f"适合方向：{direction}", "亮点："]
        for idx, h in enumerate(hl_list, 1):
            lines.append(f"{idx}. {h}")
        return "\n".join(lines)

    text = _render(items)
    while len(text) > 300 and len(items) > 3:
        items.pop()
        text = _render(items)

    if len(text) > 300:
        text = text[:300]

    return text


def generate_resume_profile(
    settings: Settings,
    sanitized_text: str,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, dict[str, Any]]:
    """调用大模型提炼简历画像 (T010)。
    返回 (提炼后的画像文本, token用量字典)。
    """
    messages = build_profile_messages(sanitized_text)
    engine = settings.judge_engine
    thinking = ("think" in engine and "no-think" not in engine)

    content, usage = call_once(
        settings,
        messages,
        transport=transport,
        thinking=thinking,
        max_tokens=600,
    )

    direction, highlights = parse_profile_response(content)
    clean_content = format_profile_text(direction, highlights)

    # 审计日志：严禁记录提示词与画像全文，仅记录字符数
    logger.info("resume_profile generated successfully", extra={"chars": len(clean_content)})

    return clean_content, usage
