import json
from pathlib import Path
from typing import Any
import httpx
import pytest

from jet.config import Settings
from jet.llm.client import ParseError
from jet.llm.resume_profile import (
    MAX_RESUME_TEXT_CHARS,
    SYSTEM_PROMPT,
    build_profile_messages,
    format_profile_text,
    generate_resume_profile,
    parse_profile_response,
)
from tests.conftest import FakeLlmHelper


def test_resume_profile_system_prompt_generalization():
    """测试提示词通用化：适用各行业求职顾问，而非局限技术岗 (需求 4)。"""
    # 适用各行业：资深求职顾问
    assert "资深求职顾问" in SYSTEM_PROMPT
    assert "技术专家" not in SYSTEM_PROMPT

    # 亮点示例涵盖各行业核心能力、代表性成果、行业经验、工具或方法
    assert "核心能力" in SYSTEM_PROMPT
    assert "代表性成果与量化结果" in SYSTEM_PROMPT
    assert "行业/领域经验" in SYSTEM_PROMPT
    assert "工具或方法" in SYSTEM_PROMPT

    # 旧的技术岗专属表述已移除
    assert "核心技术栈" not in SYSTEM_PROMPT
    assert "项目复杂度" not in SYSTEM_PROMPT


def test_build_profile_messages_max_8000_chars_truncation():
    """测试发送给大模型的脱敏正文最多 8000 字（超出截断），以控制费用 (需求 4)。"""
    # 构造 10000 字符的脱敏正文
    prefix = "A" * 8000
    suffix = "B" * 2000
    long_text = prefix + suffix
    assert len(long_text) == 10000

    messages = build_profile_messages(long_text)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    user_content = messages[1]["content"]

    # 断言包含前 8000 字符，但绝不包含截断后的第 8001 个字符
    assert prefix in user_content
    assert "B" not in user_content

    # 提取正文并严格断言字符数上限
    start_marker = "以下是候选人脱敏后的简历正文：\n\n"
    end_marker = "\n\n请根据上述正文提炼简历画像。"
    assert start_marker in user_content
    assert end_marker in user_content

    extracted_body = user_content[
        user_content.index(start_marker) + len(start_marker) : user_content.index(end_marker)
    ]
    assert len(extracted_body) == MAX_RESUME_TEXT_CHARS == 8000


def test_build_profile_messages_under_limit_preserved():
    """测试未超 8000 字符的脱敏正文完整保留。"""
    normal_text = "候选人有 5 年丰富运营与项目管理经验，主导过多个跨部门协调项目。"
    messages = build_profile_messages(normal_text)
    assert normal_text in messages[1]["content"]


def test_generate_resume_profile_truncates_output_to_300_chars(tmp_path: Path):
    """测试画像生成返回时强截断至 300 字符以内。"""
    settings = Settings(
        data_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://api.deepseek.com",
        judge_engine="deepseek-chat",
    )

    overlong_json = json.dumps(
        {
            "direction": "产品运营专家与用户增长总监",
            "highlights": [
                "具备非常丰富的跨部门沟通与实践经验，在多个复杂业务场景中主导核心运营策略并取得显著业务突破与增长。" * 3,
                "深度掌握数据分析与用户增长方法论，能够熟练运用多种分析工具进行用户画像刻画与全生命周期运营管理。" * 3,
                "主导过多款行业领先产品的冷启动与规模化增长，具备极强的大团队协同组织与项目高效推进落地执行力。" * 3,
            ],
        },
        ensure_ascii=False,
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": overlong_json}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 80},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    profile_text, usage = generate_resume_profile(settings, "脱敏正文样例", transport=transport)

    assert len(profile_text) == 300
    assert profile_text.startswith("适合方向：产品运营专家与用户增长总监")


def test_parse_and_format_profile_success():
    """测试解析成功拼接格式 (需求 3)。"""
    raw_content = json.dumps(
        {
            "direction": "Python 后端高并发架构研发",
            "highlights": [
                "6 年 Python/FastAPI 开发经验",
                "深入掌握分布式系统与 MySQL/Redis 调优",
                "主导过大规模交易系统架构设计与落地",
            ],
        },
        ensure_ascii=False,
    )

    direction, highlights = parse_profile_response(raw_content)
    assert direction == "Python 后端高并发架构研发"
    assert len(highlights) == 3

    profile_text = format_profile_text(direction, highlights)
    expected_text = (
        "适合方向：Python 后端高并发架构研发\n"
        "亮点：\n"
        "1. 6 年 Python/FastAPI 开发经验\n"
        "2. 深入掌握分布式系统与 MySQL/Redis 调优\n"
        "3. 主导过大规模交易系统架构设计与落地"
    )
    assert profile_text == expected_text
    assert len(profile_text) <= 300


def test_parse_profile_less_than_3_highlights_raises_error():
    """测试少于 3 条报错及非法输入抛 ParseError (需求 3)。"""
    # 只有 2 条亮点 -> 报错
    raw_2_items = json.dumps(
        {
            "direction": "前端工程师",
            "highlights": ["熟练 Vue", "熟练 React"],
        },
        ensure_ascii=False,
    )
    with pytest.raises(ParseError, match="少于 3 条"):
        parse_profile_response(raw_2_items)

    # 0 条亮点 -> 报错
    raw_0_items = json.dumps(
        {
            "direction": "前端工程师",
            "highlights": [],
        },
        ensure_ascii=False,
    )
    with pytest.raises(ParseError, match="少于 3 条"):
        parse_profile_response(raw_0_items)

    # 包含空白项导致有效亮点少于 3 条 -> 报错
    raw_blank_item = json.dumps(
        {
            "direction": "前端工程师",
            "highlights": ["熟练 Vue", "  ", "熟练 React"],
        },
        ensure_ascii=False,
    )
    with pytest.raises(ParseError, match="非字符串或空白项"):
        parse_profile_response(raw_blank_item)

    # direction 为空 -> 报错
    raw_empty_direction = json.dumps(
        {
            "direction": "   ",
            "highlights": ["亮点1", "亮点2", "亮点3"],
        },
        ensure_ascii=False,
    )
    with pytest.raises(ParseError, match="direction 缺失或为空"):
        parse_profile_response(raw_empty_direction)

    # 非合法 JSON -> 报错
    with pytest.raises(ParseError, match="无法解析为 JSON"):
        parse_profile_response("not a json string")

    # 空内容 -> 报错
    with pytest.raises(ParseError, match="内容为空"):
        parse_profile_response("")


def test_parse_profile_more_than_5_highlights_truncates_to_5():
    """测试多于 5 条截取前 5 条 (需求 3)。"""
    raw_7_items = json.dumps(
        {
            "direction": "全栈开发架构师",
            "highlights": [
                "亮点1",
                "亮点2",
                "亮点3",
                "亮点4",
                "亮点5",
                "亮点6_多余",
                "亮点7_多余",
            ],
        },
        ensure_ascii=False,
    )

    direction, highlights = parse_profile_response(raw_7_items)
    assert len(highlights) == 5
    assert highlights == ["亮点1", "亮点2", "亮点3", "亮点4", "亮点5"]

    profile_text = format_profile_text(direction, highlights)
    assert "5. 亮点5" in profile_text
    assert "亮点6" not in profile_text
    assert "亮点7" not in profile_text


def test_format_profile_overlong_deletes_highlights_and_truncates():
    """测试超长删减：总长超 300 字时从后往前删亮点（至少保留 3 条），仍超则截断到 300 字 (需求 1, 3)。"""
    direction = "资深系统架构师"

    # 1. 5 条亮点超长（每条 57 字）：删除第 5 条后，4 条总长 <= 300
    h_60 = "具备极强的大规模微服务体系治理能力与跨团队业务系统架构演进规划落地实操经验。"
    h_70 = h_60 + "深入掌握分布式高可用与性能调优。"
    highlights_5 = [f"{h_70}（版本{i}）" for i in range(1, 6)]

    text_5 = format_profile_text(direction, highlights_5)
    assert len(text_5) <= 300
    assert "4. " in text_5
    assert "5. " not in text_5

    # 2. 5 条亮点每条长约 95 字，删到只剩 3 条（总长约 310 字）后仍超 300 字：必须保留 3 条，截断到 300 字
    h_95 = "具备极强的大规模微服务体系治理能力与跨团队业务系统架构演进规划落地实操经验，深入掌握分布式高可用与性能调优架构设计，熟练运用各类云原生组件推进复杂系统落地与调优。"
    highlights_very_long = [f"{h_95}（条目{i}：持续推动工程架构演进与研发效能提升）" for i in range(1, 6)]
    text_very_long = format_profile_text(direction, highlights_very_long)
    assert len(text_very_long) == 300
    # 至少保留 3 条亮点（前 3 条序号保留）
    assert "1. " in text_very_long
    assert "2. " in text_very_long
    assert "3. " in text_very_long
    assert "4. " not in text_very_long
    assert "5. " not in text_very_long


def test_parse_profile_code_block_wrapping():
    """测试代码块包裹（带 ```json 或 ```）能正常解析 (需求 3)。"""
    data = {
        "direction": "产品总监",
        "highlights": ["业务洞察力强", "团队管理经验丰富", "推动多个 0 到 1 重点产品"],
    }
    # 带 ```json 包裹
    wrapped_json = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    direction, highlights = parse_profile_response(wrapped_json)
    assert direction == "产品总监"
    assert len(highlights) == 3

    # 仅带 ``` 包裹
    wrapped_plain = f"```\n{json.dumps(data, ensure_ascii=False)}\n```"
    direction2, highlights2 = parse_profile_response(wrapped_plain)
    assert direction2 == "产品总监"
    assert len(highlights2) == 3


def test_generate_resume_profile_request_contains_json(tmp_path: Path):
    """测试调用 generate_resume_profile 时发出的请求包含 'json' (需求 3)。"""
    settings = Settings(
        data_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://api.deepseek.com",
        judge_engine="deepseek-chat",
    )

    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        content = json.dumps(
            {
                "direction": "Python 资深工程师",
                "highlights": ["6年经验", "熟练FastAPI", "精通MySQL"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    profile_text, usage = generate_resume_profile(settings, "候选人脱敏简历正文", transport=transport)

    assert len(recorded_requests) == 1
    req_body_str = recorded_requests[0].content.decode("utf-8")
    req_json = json.loads(req_body_str)

    # response_format 为 json_object
    assert req_json["response_format"] == {"type": "json_object"}

    # 断言请求体内容中明确包含 'json'（不区分大小写）
    assert "json" in req_body_str.lower()
    assert any("json" in str(m.get("content", "")).lower() for m in req_json.get("messages", []))


def test_fake_llm_helper_deepseek_json_restriction():
    """测试 FakeLlmHelper 正确模拟 DeepSeek 限制：带 response_format 但 messages 无 json 返回 HTTP 400 (需求 2)。"""
    fake_llm = FakeLlmHelper()

    # 1. messages 不含 "json" -> 400
    bad_request = httpx.Request(
        "POST",
        "https://api.deepseek.com/chat/completions",
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "请提炼一段纯文本简历画像"}],
            "response_format": {"type": "json_object"},
        },
    )
    resp_400 = fake_llm.handler(bad_request)
    assert resp_400.status_code == 400
    assert "Prompt must contain the word 'json' in some form to use 'response_format' of type 'json_object'." in resp_400.text

    # 2. messages 包含 "json" -> 200
    good_request = httpx.Request(
        "POST",
        "https://api.deepseek.com/chat/completions",
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "请提炼简历画像，输出为 JSON 格式"}],
            "response_format": {"type": "json_object"},
        },
    )
    resp_200 = fake_llm.handler(good_request)
    assert resp_200.status_code == 200
