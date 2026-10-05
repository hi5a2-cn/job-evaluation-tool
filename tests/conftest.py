from typing import Any
from collections.abc import Generator
import dataclasses
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
import httpx
import pytest

from jet.api.app import create_app
from jet.config import DEFAULT_DATA_DIR, Settings, load_settings


def _snapshot_dir(dir_path: Path) -> set[tuple[str, int, int]]:
    if not dir_path.exists() or not dir_path.is_dir():
        return set()
    snapshot = set()
    for p in dir_path.rglob("*"):
        if p.is_file():
            # 排除本机常驻 Jet 守护进程（local.jet.serve）写入的 WAL 临时日志
            if p.name in ("jet.db-wal", "jet.db-shm"):
                continue
            stat = p.stat()
            rel = str(p.relative_to(dir_path))
            snapshot.add((rel, stat.st_size, stat.st_mtime_ns))
    return snapshot


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config._jet_initial_snapshot = _snapshot_dir(DEFAULT_DATA_DIR)  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    initial_snapshot = getattr(session.config, "_jet_initial_snapshot", set())
    current_snapshot = _snapshot_dir(DEFAULT_DATA_DIR)
    if current_snapshot != initial_snapshot:
        print(f"\n[CRITICAL ERROR] 测试写入了真实数据目录: {DEFAULT_DATA_DIR}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "jet-data"


@pytest.fixture(autouse=True)
def isolate_env(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JET_DATA_DIR", str(data_dir))
    for key in list(os.environ.keys()):
        if key.startswith("JET_") and key != "JET_DATA_DIR":
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return load_settings(data_dir)


@pytest.fixture
def client(settings: Settings) -> Generator[TestClient, None, None]:
    app = create_app(settings, admin_secret="test-admin")
    # 模拟本机回环地址（/internal/pair-code 只接受 127.0.0.1）
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


@pytest.fixture
def paired_client(client: TestClient) -> tuple[TestClient, dict[str, str]]:
    app = client.app
    pairing_codes = app.state.pairing_codes
    code = pairing_codes.issue()
    origin = "chrome-extension://testextid"
    resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
    assert resp.status_code == 200, f"Pairing failed in fixture: {resp.text}"
    token = resp.json()["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": origin,
    }
    return client, headers


class FakeLlmHelper:
    """Configurable mock transport for LLM testing."""

    def __init__(self) -> None:
        self.call_count = 0
        self.mode = "fit"
        self.custom_reasons: list[str] | None = None
        self.requests: list[httpx.Request] = []
        self.assist_suggestions: list[dict[str, Any]] | None = None
        self.assist_questions: list[str] | None = None
        self.assist_experience_note: str | None = None
        self.prejudge_results: list[dict[str, Any]] | None = None

    def set_prejudge(self, results: list[dict[str, Any]] | None = None) -> None:
        self.prejudge_results = results

    def set_assist(
        self,
        suggestions: list[dict[str, Any]] | None = None,
        questions: list[str] | None = None,
        experience_note: str | None = None,
    ) -> None:
        self.mode = "assist"
        self.assist_suggestions = suggestions
        self.assist_questions = questions
        self.assist_experience_note = experience_note

    def set_fit(self, reasons: list[str] | None = None) -> None:
        self.mode = "fit"
        self.custom_reasons = reasons or ["技能匹配度高", "薪资符合预期"]

    def set_unfit(self, reasons: list[str] | None = None) -> None:
        self.mode = "unfit"
        self.custom_reasons = reasons or ["技术栈不匹配", "行业方向不符"]

    def set_unsure(self, reasons: list[str] | None = None) -> None:
        self.mode = "unsure"
        self.custom_reasons = reasons or ["职责混杂需要进一步核实"]

    def set_apply(self, reasons: list[str] | None = None) -> None:
        self.mode = "fit"
        self.custom_reasons = reasons or ["技能匹配度高", "薪资符合预期"]

    def set_try(self, reasons: list[str] | None = None) -> None:
        self.mode = "try"
        self.custom_reasons = reasons or ["大体符合可以一试"]

    def set_check(self, reasons: list[str] | None = None) -> None:
        self.mode = "unsure"
        self.custom_reasons = reasons or ["职责混杂需要进一步核实"]

    def set_skip(self, reasons: list[str] | None = None) -> None:
        self.mode = "unfit"
        self.custom_reasons = reasons or ["技术栈不匹配", "行业方向不符"]

    def set_risk_signals(
        self,
        signals: list[dict[str, Any]] | None = None,
        verdict: str = "skip",
        reason: str = "命中非法金融风险信号",
    ) -> None:
        self.mode = "risk_signals"
        self.risk_signals = signals or [{"type": "非法金融", "description": "按交易量提成", "quote": "每天600手为标准"}]
        self.custom_verdict = verdict
        self.custom_reason = reason

    def set_invalid_json(self) -> None:
        self.mode = "invalid_json"

    def set_timeout(self) -> None:
        self.mode = "timeout"

    def set_http_500(self) -> None:
        self.mode = "http_500"

    def set_connect_error(self) -> None:
        self.mode = "connect_error"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        self.requests.append(request)

        if self.mode == "timeout":
            raise httpx.ReadTimeout("Mock LLM timeout", request=request)
        if self.mode == "connect_error":
            raise httpx.ConnectError("Mock connection refused", request=request)

        req_body: dict[str, Any] = {}
        try:
            req_body = json.loads(request.content.decode("utf-8"))
        except Exception:
            pass

        # 模拟 DeepSeek 限制：带 response_format.type == "json_object" 时，
        # 所有 messages 内容都不含 "json"（不区分大小写）时，返回 HTTP 400
        resp_format = req_body.get("response_format")
        if isinstance(resp_format, dict) and resp_format.get("type") == "json_object":
            raw_msgs = req_body.get("messages", [])
            has_json = any(
                "json" in str(m.get("content", "")).lower()
                for m in raw_msgs
                if isinstance(m, dict)
            )
            if not has_json:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "Prompt must contain the word 'json' in some form to use 'response_format' of type 'json_object'.",
                            "type": "invalid_request_error",
                            "param": None,
                            "code": "invalid_request_error",
                        }
                    },
                )

        if self.mode == "http_500":
            return httpx.Response(500, json={"error": "Internal Server Error"})
        if self.mode == "invalid_json":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "not a valid json response"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                },
            )
        # Determine whether request is for v1, v2, v3, or v4/v5 prompt, or assist prompt, or prejudge, or resume_profile
        is_assist = False
        is_v4 = False
        is_v3 = False
        is_v2 = False
        is_prejudge = False
        is_resume_profile = False
        try:
            max_tokens = req_body.get("max_tokens")
            msgs = req_body.get("messages", [])
            is_prejudge = any(
                "粗略信息" in m.get("content", "")
                or "不构成正式结论" in m.get("content", "")
                or "待预判岗位列表" in m.get("content", "")
                for m in msgs
            )
            is_assist = not is_prejudge and any(
                "【聊天记录】" in m.get("content", "")
                or "语气规则" in m.get("content", "")
                or "经历素材" in m.get("content", "")
                or "referenced_experience_ids" in m.get("content", "")
                for m in msgs
            )
            is_resume_profile = (
                not is_prejudge
                and not is_assist
                and any(
                    "简历画像" in m.get("content", "")
                    or "候选人脱敏后的简历正文" in m.get("content", "")
                    for m in msgs
                )
            )
            is_v4 = not is_assist and not is_prejudge and not is_resume_profile and any(
                "工作类型两层分类" in m.get("content", "")
                or "work_intensity" in m.get("content", "")
                or "建议四档及含义" in m.get("content", "")
                or "apply|try|check|skip" in m.get("content", "")
                or "偏好城市只是偏好" in m.get("content", "")
                or "偏好城市（为空表示城市都可以）" in m.get("content", "")
                for m in msgs
            )
            is_v3 = not is_assist and not is_v4 and not is_prejudge and not is_resume_profile and any(
                "requirement_type" in m.get("content", "") or "verdict_reason" in m.get("content", "")
                for m in msgs
            )
            is_v2 = not is_assist and not is_v4 and not is_v3 and not is_prejudge and not is_resume_profile and ((max_tokens == 1200) or any("先事实后建议" in m.get("content", "") for m in msgs))
        except Exception:
            pass

        if is_prejudge:
            if self.prejudge_results is not None:
                content = json.dumps(self.prejudge_results, ensure_ascii=False)
            else:
                import re
                job_ids = []
                for m in msgs:
                    c = m.get("content", "")
                    job_ids.extend(re.findall(r'\[(\d+)\]', c))
                if not job_ids:
                    job_ids = ["1"]
                items = [
                    {"id": jid, "level": "open", "reason": f"预判通过：{jid} 薪资与方向契合"}
                    for jid in job_ids
                ]
                content = json.dumps(items, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 150, "completion_tokens": 50},
                },
            )

        if is_resume_profile:
            content = json.dumps(
                {
                    "direction": "资深后端开发专家与技术负责人",
                    "highlights": [
                        "精通高并发与分布式架构设计",
                        "具备大型交易系统实战调优经验",
                        "深入掌握微服务治理与云原生生态",
                    ],
                },
                ensure_ascii=False,
            )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 50},
                },
            )

        if self.mode == "assist" or (self.mode == "fit" and is_assist):
            suggestions = self.assist_suggestions if self.assist_suggestions is not None else [
                {
                    "version": 1,
                    "tone_desc": "自然直接",
                    "text": "好的呀，我这就发您一份。请问贵公司在尼日利亚那边的具体业务开展情况大概是怎样的呢？",
                    "referenced_experience_ids": [1],
                },
                {
                    "version": 2,
                    "tone_desc": "沉稳专业",
                    "text": "嗯嗯好的，简历已通过附件发您，您查收看看。另外想先确认一下该岗位主要负责对接哪些类型的客户？",
                    "referenced_experience_ids": [1],
                },
            ]
            questions = self.assist_questions if self.assist_questions is not None else [
                "尼日利亚那边的业务目前是刚起步还是已经有成熟渠道？",
                "想确认一下平时主要对接的是海外代理商还是终端客户？",
            ]
            content_dict: dict[str, Any] = {
                "suggestions": suggestions,
                "questions": questions,
            }
            if self.assist_experience_note is not None:
                content_dict["experience_note"] = self.assist_experience_note
            content = json.dumps(content_dict, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 40},
                },
            )

        if self.mode == "risk_signals":
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "疑似非法金融风险", "quotes": []},
                        "work_type": {"value": "其他", "subtype": None, "secondary": [], "quotes": []},
                        "sales_level": {"value": "高", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "无法判断", "gap": ""},
                        "work_intensity": {"value": "未提及", "quotes": []},
                        "risk_signals": getattr(self, "risk_signals", []),
                    },
                    "verdict": getattr(self, "custom_verdict", "skip"),
                    "derivation": [getattr(self, "custom_reason", "命中风险信号")],
                    "verdict_reason": getattr(self, "custom_reason", "命中风险信号"),
                    "hr_questions": [],
                },
                ensure_ascii=False,
            )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 25},
                },
            )

        if self.mode == "try":
            if is_v4:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "大体符合画像要求", "quotes": []},
                            "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
                            "sales_level": {"value": "低", "signals": []},
                            "experience": {"requirement": None, "requirement_type": "优先", "value": "差一点", "gap": "年限稍短"},
                            "work_intensity": {"value": "双休", "quotes": []},
                            "risk_signals": [],
                        },
                        "verdict": "try",
                        "derivation": self.custom_reasons or ["经验稍欠但可以一试"],
                        "verdict_reason": (self.custom_reasons or ["经验稍欠但可以一试"])[0],
                        "hr_questions": ["实际对接客户的比例大概是多少？", "是否有明确的业绩指标？"],
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps({"verdict": "fit", "reasons": ["大体符合"]}, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 25},
                },
            )

        if self.mode == "unsure":
            if is_v4:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "职责包含部分技术与部分销售", "quotes": []},
                            "work_type": {"value": "运营", "subtype": "用户运营", "secondary": [], "quotes": []},
                            "sales_level": {"value": "中", "signals": [{"signal": "维护客户关系", "quote": "维护客户关系"}]},
                            "experience": {"requirement": None, "requirement_type": "硬性", "value": "差一点", "gap": "缺乏大型项目经验"},
                            "work_intensity": {"value": "未提及", "quotes": []},
                            "risk_signals": [],
                        },
                        "verdict": "check",
                        "derivation": self.custom_reasons or ["职责混杂建议进一步核实"],
                        "verdict_reason": (self.custom_reasons or ["职责混杂建议进一步核实"])[0],
                        "hr_questions": ["实际对接银行客户的时间大概占多少？", "有没有个人业绩或拉新指标？"],
                    },
                    ensure_ascii=False,
                )
            elif is_v3:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "职责包含部分技术与部分销售", "quotes": []},
                            "work_type": {"value": "运营", "secondary": [], "quotes": []},
                            "sales_level": {"value": "中", "signals": [{"signal": "维护客户关系", "quote": "维护客户关系"}]},
                            "experience": {"requirement": None, "requirement_type": "硬性", "value": "差一点", "gap": "缺乏大型项目经验"},
                            "overtime": {"value": "未提及", "quotes": []},
                        },
                        "verdict": "unsure",
                        "derivation": self.custom_reasons or ["职责混杂建议进一步核实"],
                        "verdict_reason": (self.custom_reasons or ["职责混杂建议进一步核实"])[0],
                    },
                    ensure_ascii=False,
                )
            elif is_v2:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "职责包含部分技术与部分销售", "quotes": []},
                            "work_type": {"value": "运营", "quotes": []},
                            "sales_level": {"value": "中", "signals": [{"signal": "维护客户关系", "quote": "维护客户关系"}]},
                            "experience": {"requirement": None, "value": "差一点", "gap": "缺乏大型项目经验"},
                            "overtime": {"value": "未提及", "quotes": []},
                        },
                        "verdict": "unsure",
                        "derivation": self.custom_reasons or ["职责混杂建议进一步核实"],
                    },
                    ensure_ascii=False,
                )
            else:
                reasons = self.custom_reasons or ["存疑"]
                content = json.dumps({"verdict": "unsure", "reasons": reasons}, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 25},
                },
            )

        if self.mode == "unfit":
            if is_v4:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "白话职责概括", "quotes": []},
                            "work_type": {"value": "市场与销售", "subtype": "销售与商务拓展", "secondary": [], "quotes": []},
                            "sales_level": {"value": "高", "signals": [{"signal": "获客转化", "quote": "获客转化"}]},
                            "experience": {"requirement": "要求5年经验", "requirement_type": "硬性", "value": "不满足", "gap": "年限不足"},
                            "work_intensity": {"value": "高强度", "quotes": []},
                            "risk_signals": [],
                        },
                        "verdict": "skip",
                        "derivation": self.custom_reasons or ["销售成分高与画像底线冲突"],
                        "verdict_reason": (self.custom_reasons or ["销售成分高与画像底线冲突"])[0],
                        "hr_questions": [],
                    },
                    ensure_ascii=False,
                )
            elif is_v3:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "白话职责概括", "quotes": []},
                            "work_type": {"value": "销售", "secondary": [], "quotes": []},
                            "sales_level": {"value": "高", "signals": [{"signal": "获客转化", "quote": "获客转化"}]},
                            "experience": {"requirement": "要求5年经验", "requirement_type": "硬性", "value": "不满足", "gap": "年限不足"},
                            "overtime": {"value": "有", "quotes": []},
                        },
                        "verdict": "unfit",
                        "derivation": self.custom_reasons or ["销售成分高与画像底线冲突"],
                        "verdict_reason": (self.custom_reasons or ["销售成分高与画像底线冲突"])[0],
                    },
                    ensure_ascii=False,
                )
            elif is_v2:
                content = json.dumps(
                    {
                        "facts": {
                            "summary": {"text": "白话职责概括", "quotes": []},
                            "work_type": {"value": "销售", "quotes": []},
                            "sales_level": {"value": "高", "signals": [{"signal": "获客转化", "quote": "获客转化"}]},
                            "experience": {"requirement": None, "value": "不满足", "gap": "年限不足"},
                            "overtime": {"value": "有", "quotes": []},
                        },
                        "verdict": "unfit",
                        "derivation": self.custom_reasons or ["销售成分高与画像底线冲突"],
                    },
                    ensure_ascii=False,
                )
            else:
                reasons = self.custom_reasons or ["不符合要求"]
                content = json.dumps({"verdict": "unfit", "reasons": reasons}, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 25},
                },
            )

        # Default: "fit"
        if is_v4:
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "白话职责概括", "quotes": []},
                        "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                        "work_intensity": {"value": "双休", "quotes": []},
                        "risk_signals": [],
                    },
                    "verdict": "apply",
                    "derivation": self.custom_reasons or ["各项符合画像要求", "薪资满意"],
                    "verdict_reason": (self.custom_reasons or ["各项符合画像要求", "薪资满意"])[0],
                    "hr_questions": [],
                },
                ensure_ascii=False,
            )
        elif is_v3:
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "白话职责概括", "quotes": []},
                        "work_type": {"value": "技术支持", "secondary": [], "quotes": []},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                        "overtime": {"value": "未提及", "quotes": []},
                    },
                    "verdict": "fit",
                    "derivation": self.custom_reasons or ["各项符合画像要求", "薪资满意"],
                    "verdict_reason": (self.custom_reasons or ["各项符合画像要求", "薪资满意"])[0],
                },
                ensure_ascii=False,
            )
        elif is_v2:
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "白话职责概括", "quotes": []},
                        "work_type": {"value": "技术支持", "quotes": []},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "value": "满足", "gap": ""},
                        "overtime": {"value": "未提及", "quotes": []},
                    },
                    "verdict": "fit",
                    "derivation": self.custom_reasons or ["各项符合画像要求", "薪资满意"],
                },
                ensure_ascii=False,
            )
        else:
            reasons = self.custom_reasons or ["符合画像要求", "薪资满意"]
            content = json.dumps({"verdict": "fit", "reasons": reasons}, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 150,
                    "prompt_cache_hit_tokens": 50,
                    "completion_tokens": 30,
                },
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        # 每次请求时才取当前的 handler：测试里在应用建好之后再替换 fake_llm.handler 也会生效
        return httpx.MockTransport(lambda request: self.handler(request))


@pytest.fixture
def fake_llm() -> FakeLlmHelper:
    return FakeLlmHelper()


@pytest.fixture
def llm_client(
    settings: Settings, fake_llm: FakeLlmHelper
) -> Generator[tuple[TestClient, dict[str, str]], None, None]:
    # 复核会多一次调用，打乱这些测试的调用计数：公用 fixture 默认关闭复核，复核另见 tests/api/test_review.py
    llm_settings = dataclasses.replace(settings, llm_api_key="test-api-key", review_enabled=False)
    app = create_app(llm_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        pairing_codes = app.state.pairing_codes
        code = pairing_codes.issue()
        origin = "chrome-extension://testextid"
        resp = c.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert resp.status_code == 200
        token = resp.json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": origin,
        }
        yield c, headers
