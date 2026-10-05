import base64
import dataclasses
import io
import json
import math
from pathlib import Path
import httpx
import pypdf
import pytest
from starlette.testclient import TestClient

from jet.api.app import create_app
from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.domain.resume import get_resume_slot


def _make_ascii_pdf_bytes(lines: list[str]) -> bytes:
    """生成带有指定文本的有效最小 ASCII PDF 字节流。"""
    stream_content = "BT\n/F1 12 Tf\n50 750 Td\n14 TL\n"
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i == 0:
            stream_content += f"({escaped}) Tj\n"
        else:
            stream_content += f"T* ({escaped}) Tj\n"
    stream_content += "ET\n"
    stream_bytes = stream_content.encode("ascii")

    obj1 = b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n"
    obj2 = b"2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
    obj3 = b"3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R>>\nendobj\n"
    obj4 = b"4 0 obj\n<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>\nendobj\n"
    obj5 = f"5 0 obj\n<</Length {len(stream_bytes)}>>\nstream\n".encode("ascii") + stream_bytes + b"endstream\nendobj\n"

    header = b"%PDF-1.4\n"
    pos1 = len(header)
    pos2 = pos1 + len(obj1)
    pos3 = pos2 + len(obj2)
    pos4 = pos3 + len(obj3)
    pos5 = pos4 + len(obj4)
    xref_pos = pos5 + len(obj5)

    xref = (
        f"xref\n0 6\n"
        f"0000000000 65535 f \n"
        f"{pos1:010d} 00000 n \n"
        f"{pos2:010d} 00000 n \n"
        f"{pos3:010d} 00000 n \n"
        f"{pos4:010d} 00000 n \n"
        f"{pos5:010d} 00000 n \n"
    ).encode("ascii")

    trailer = f"trailer\n<</Size 6 /Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


def _make_blank_pdf_bytes() -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_resume_routes_auth_required(client: TestClient):
    """测试所有简历接口均需要配对鉴权 (401)。"""
    assert client.post("/v1/resumes/upload", json={}).status_code == 401
    assert client.post("/v1/resumes/1/regenerate", json={}).status_code == 401
    assert client.get("/v1/resumes").status_code == 401
    assert client.put("/v1/resumes", json={"items": []}).status_code == 401
    assert client.delete("/v1/resumes/1").status_code == 401


def test_resume_upload_success_and_body_whitelist(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """测试正常上传文字版 PDF 成功生成画像，并断言外发大模型的请求体绝对不含隐私信息 (FR-010, FR-015, T013)。"""
    p_client, headers = paired_client

    # 包含虚构个人隐私信息与足够字数的正文 (> 50 字)
    lines = [
        "Li Ming",
        "Phone: 13800000000",
        "Email: liming@example.com",
        "ID Card: 110101199001011234",
        "Software Engineer with 6 years experience in Python and FastAPI backend development",
        "Skilled in MySQL, Redis, Kafka, Distributed Systems and Cloud Computing architecture",
    ]
    pdf_bytes = _make_ascii_pdf_bytes(lines)
    b64_pdf = base64.b64encode(pdf_bytes).decode("ascii")

    recorded_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        content = json.dumps(
            {
                "direction": "Python 后端高并发架构研发",
                "highlights": [
                    "6 年 Python/FastAPI 开发经验",
                    "深入掌握分布式系统与 MySQL/Redis 调优",
                    "具备高可用架构与性能调优实战经验",
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 60},
            },
        )

    # 挂载 MockTransport
    transport = httpx.MockTransport(mock_handler)
    p_client.app.state.llm_transport = transport
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key="test-fake-key")

    payload = {
        "slot": 1,
        "name": "后端开发-通用版",
        "pdf_base64": b64_pdf,
        "self_name": "Li Ming",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["slot"] == 1
    assert data["name"] == "后端开发-通用版"
    assert "适合方向：Python 后端高并发架构研发" in data["profile"]
    assert data["text_chars"] > 50

    # 1. 核心安全断言：发往大模型的 Prompt 绝对不含虚构姓名、手机号、邮箱、身份证号、简历名称、文件名，并包含 'json'
    assert len(recorded_requests) == 1
    req_body_str = recorded_requests[0].content.decode("utf-8")

    assert "Li Ming" not in req_body_str
    assert "13800000000" not in req_body_str
    assert "liming@example.com" not in req_body_str
    assert "110101199001011234" not in req_body_str
    assert "后端开发-通用版" not in req_body_str
    assert "LiMing_Backend_Resume.pdf" not in req_body_str
    assert "json" in req_body_str.lower()

    # 2. 验证数据库中正确保存了名称、脱敏正文与画像
    conn = open_db(data_dir)
    slot_row = get_resume_slot(conn, "me", 1)
    assert slot_row is not None
    assert slot_row["name"] == "后端开发-通用版"
    assert slot_row["profile"] == data["profile"]
    assert slot_row["resume_text"] is not None
    assert "13800000000" not in slot_row["resume_text"]
    conn.close()


def test_resume_upload_scan_empty_pdf_blocked(paired_client: tuple[TestClient, dict[str, str]]):
    """测试上传扫描件或空白 PDF 拦截并报 422 no_text (FR-006, T013)。"""
    p_client, headers = paired_client
    blank_bytes = _make_blank_pdf_bytes()
    b64_pdf = base64.b64encode(blank_bytes).decode("ascii")

    payload = {
        "slot": 1,
        "name": "扫描件简历",
        "pdf_base64": b64_pdf,
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "no_text"
    assert "无法读取文字" in resp.json()["message"]


def test_resume_upload_corrupt_pdf_blocked(paired_client: tuple[TestClient, dict[str, str]]):
    """测试上传损坏 PDF 拦截并报 422 pdf_invalid (FR-010, T013)。"""
    p_client, headers = paired_client
    b64_pdf = base64.b64encode(b"corrupt non-pdf bytes").decode("ascii")

    payload = {
        "slot": 1,
        "name": "损坏简历",
        "pdf_base64": b64_pdf,
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "pdf_invalid"


def test_resume_upload_b64_too_large_blocked_before_decode(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 Base64 超过 5MB 上限时在解码前直接拦截报 file_too_large，且不进行解码。"""
    p_client, headers = paired_client
    max_b64_len = math.ceil(5 * 1024 * 1024 / 3) * 4
    # 使用包含非法 base64 字符的内容；若提前根据长度拦截则报 file_too_large，若先解码则会报 pdf_invalid
    oversized_b64 = "!" * (max_b64_len + 1)

    payload = {
        "slot": 1,
        "name": "超大简历",
        "pdf_base64": oversized_b64,
        "self_name": "李明",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "file_too_large"
    assert "5MB" in resp.json()["message"]


def test_resume_upload_invalid_b64_chars_returns_pdf_invalid(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 Base64 包含非法字符时通过 validate=True 识别并报 422 pdf_invalid。"""
    p_client, headers = paired_client
    # 含有非 base64 字母表字符 '!'
    invalid_b64 = "bm90X3ZhbGlkX2Jhc2U2NA==!!!"

    payload = {
        "slot": 1,
        "name": "非法字符简历",
        "pdf_base64": invalid_b64,
        "self_name": "李明",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "pdf_invalid"


def test_resume_upload_no_name_blocked(paired_client: tuple[TestClient, dict[str, str]]):
    """测试三来源均无姓名时拦截并报 422 self_name_required (FR-008, T013)。"""
    p_client, headers = paired_client
    # 正文不含人名特征（且 users.display_name 为 'me'）
    lines = [
        "Software Engineering Project Summary Document",
        "Phone: 13800000000",
        "Email: dev@example.com",
        "Architecture overview and high concurrency distributed caching system implementation",
        "Detailed performance tuning report for MySQL indexing and Redis pipeline optimization",
    ]
    b64_pdf = base64.b64encode(_make_ascii_pdf_bytes(lines)).decode("ascii")

    payload = {
        "slot": 2,
        "name": "无姓名简历",
        "pdf_base64": b64_pdf,
        "self_name": None,
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "self_name_required"
    assert "没有识别出你的姓名" in resp.json()["message"]


def test_resume_upload_no_llm_key_degrade(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """测试未配置 API Key 时降级保存文字并返回 409 no_llm_key (FR-010, T013)。"""
    p_client, headers = paired_client
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key=None)  # 未配置 Key

    lines = [
        "Li Ming",
        "Phone: 13800000000",
        "Software Engineer with 5 years backend development experience in Python",
        "FastAPI distributed architecture and performance tuning skills overview",
    ]
    b64_pdf = base64.b64encode(_make_ascii_pdf_bytes(lines)).decode("ascii")

    payload = {
        "slot": 2,
        "name": "无Key测试",
        "pdf_base64": b64_pdf,
        "self_name": "Li Ming",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "no_llm_key"
    assert data["has_text"] is True

    # 验证文字已安全保存入库
    conn = open_db(data_dir)
    slot_row = get_resume_slot(conn, "me", 2)
    assert slot_row is not None
    assert slot_row["name"] == "无Key测试"
    assert slot_row["resume_text"] is not None
    conn.close()


def test_resume_upload_quota_exhausted_degrade(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """测试额度耗尽时降级保存文字并返回 429 quota_exhausted (FR-010, T013)。"""
    p_client, headers = paired_client
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key="test-key")

    # 将今日额度设为 0
    conn = open_db(data_dir)
    conn.execute("UPDATE user_settings SET daily_resume_profile_limit = 0 WHERE user_id = 'me'")
    conn.close()

    lines = [
        "Li Ming",
        "Phone: 13800000000",
        "Software Engineer with 5 years backend development experience in Python",
        "FastAPI distributed architecture and performance tuning skills overview",
    ]
    b64_pdf = base64.b64encode(_make_ascii_pdf_bytes(lines)).decode("ascii")

    payload = {
        "slot": 3,
        "name": "配额耗尽测试",
        "pdf_base64": b64_pdf,
        "self_name": "Li Ming",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 429
    data = resp.json()
    assert data["error"] == "quota_exhausted"
    assert data["has_text"] is True

    # 验证文字已安全保存入库
    conn = open_db(data_dir)
    slot_row = get_resume_slot(conn, "me", 3)
    assert slot_row is not None
    assert slot_row["name"] == "配额耗尽测试"
    assert slot_row["resume_text"] is not None
    conn.close()


def test_resume_regenerate_flow(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """测试重新生成简历画像 (FR-011, T013)。"""
    p_client, headers = paired_client

    # 1. 尝试对尚未上传过的 slot 重新生成 -> 404
    resp_404 = p_client.post("/v1/resumes/1/regenerate", json={}, headers=headers)
    assert resp_404.status_code == 404
    assert resp_404.json()["error"] == "resume_not_found"

    # 2. 先往 slot 1 写入脱敏文字
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at) "
        "VALUES ('me', 1, '原简历名称', 'Li Ming Software Engineer Python FastAPI backend systems', '旧画像', ?)",
        (now_str,),
    )
    conn.close()

    def mock_handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "direction": "重新生成的画像方向",
                "highlights": [
                    "具备分布式研发经验",
                    "熟悉主流微服务架构设计",
                    "掌握高并发缓存与数据库优化",
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": content
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    p_client.app.state.llm_transport = httpx.MockTransport(mock_handler)
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key="test-key")

    # 恢复额度
    conn = open_db(data_dir)
    conn.execute("UPDATE user_settings SET daily_resume_profile_limit = 10 WHERE user_id = 'me'")
    conn.close()

    resp_regen = p_client.post("/v1/resumes/1/regenerate", json={"self_name": "Li Ming"}, headers=headers)
    assert resp_regen.status_code == 200
    data = resp_regen.json()
    assert data["slot"] == 1
    assert data["name"] == "原简历名称"
    assert "重新生成的画像方向" in data["profile"]


def test_resume_get_put_delete_lifecycle(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """测试简历 GET（不泄露全文）、PUT（修改保留原文）与 DELETE（彻底物理删除）全流程 (FR-012, FR-013, FR-014)。"""
    p_client, headers = paired_client

    # 1. 预先录入两份简历（包含 resume_text）
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at) "
        "VALUES ('me', 1, '后端简历', '后端简历原始敏感文字已脱敏内容1', '后端画像', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at) "
        "VALUES ('me', 2, '数据简历', '数据简历原始文字2', '数据画像', ?)",
        (now_str,),
    )
    conn.close()

    # 2. GET 列表：绝不包含 resume_text
    resp_get = p_client.get("/v1/resumes", headers=headers)
    assert resp_get.status_code == 200
    items = resp_get.json()
    assert len(items) == 2
    for item in items:
        assert "resume_text" not in item
        assert "has_text" in item
        assert item["has_text"] is True
        assert item["text_chars"] > 0

    # 3. PUT 修改 slot 1 的名称与画像（≤300 字）
    put_payload = {
        "items": [
            {
                "slot": 1,
                "name": "后端简历-微调版",
                "profile": "适合方向：云原生与分布式后端架构。\n亮点：\n1. 具备大规模集群治理经验。",
            },
            {
                "slot": 2,
                "name": "数据简历",
                "profile": "数据画像",
            },
        ]
    }
    resp_put = p_client.put("/v1/resumes", json=put_payload, headers=headers)
    assert resp_put.status_code == 200
    assert resp_put.json()["ok"] is True

    # 验证底层已保存的 resume_text 完好无损保留
    conn = open_db(data_dir)
    slot1 = get_resume_slot(conn, "me", 1)
    assert slot1["name"] == "后端简历-微调版"
    assert "云原生与分布式" in slot1["profile"]
    assert slot1["resume_text"] == "后端简历原始敏感文字已脱敏内容1"
    conn.close()

    # 4. DELETE 删除 slot 2
    resp_del = p_client.delete("/v1/resumes/2", headers=headers)
    assert resp_del.status_code == 200
    assert resp_del.json() == {"ok": True, "slot": 2}

    # 验证 slot 2 被彻底物理删除
    conn = open_db(data_dir)
    assert get_resume_slot(conn, "me", 2) is None
    all_resumes = conn.execute("SELECT slot FROM resume_slots WHERE user_id = 'me'").fetchall()
    assert len(all_resumes) == 1
    assert all_resumes[0]["slot"] == 1
    conn.close()


def test_resume_upload_layout_variants_body_whitelist(
    paired_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
):
    """测试虚构简历文字样例覆盖：'李 明' 空格拆分、'李明 | 138 0000 0000 | liming@example.com' 同一行、
    '姓 名：李明'、首行为'个人简历'；断言发给大模型的请求体里绝对不含 李明 / 李 明 / 手机号 / 邮箱。
    """
    p_client, headers = paired_client

    recorded_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        content = json.dumps(
            {
                "direction": "分布式后端",
                "highlights": [
                    "具备高并发经验",
                    "精通异步编程与微服务",
                    "主导过核心交易系统架构",
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": content
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    p_client.app.state.llm_transport = transport
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key="test-key")

    mock_text = (
        "个人简历\n"
        "李 明 | 138 0000 0000 | liming@example.com\n"
        "姓 名：李明\n"
        "电话：138-0000-0000 邮箱：liming@example.com\n"
        "李 明 具备 6 年分布式与微服务架构经验，负责核心业务模块设计与性能优化\n"
        "精通 Python FastAPI 与异步编程体系，主导过大规模交易系统架构设计"
    )
    monkeypatch.setattr("jet.api.routes.extract_text_from_pdf_bytes", lambda _b: mock_text)

    valid_ascii_lines = [
        "Sample valid pdf header line with sufficient characters",
        "More ASCII lines to ensure valid PDF byte sequence generation",
        "Python backend development with distributed microservices architecture",
    ]
    pdf_bytes = _make_ascii_pdf_bytes(valid_ascii_lines)
    b64_pdf = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "slot": 3,
        "name": "排版测试简历",
        "pdf_base64": b64_pdf,
        "self_name": "李明",
    }
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 200

    assert len(recorded_requests) == 1
    req_body_str = recorded_requests[0].content.decode("utf-8")

    # 核心安全断言：绝对不含 李明 / 李 明 / 手机号 / 邮箱，并包含 'json'
    assert "李明" not in req_body_str
    assert "李 明" not in req_body_str
    assert "138 0000 0000" not in req_body_str
    assert "138-0000-0000" not in req_body_str
    assert "13800000000" not in req_body_str
    assert "liming@example.com" not in req_body_str
    assert "json" in req_body_str.lower()


def test_upload_ignores_legacy_filename_field(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """体检第 79 条：请求体不再有 filename；未刷新的旧插件多带这个字段时照常上传，文件名不入库也不外发。"""
    p_client, headers = paired_client
    lines = [
        "Software Engineer with 6 years experience in Python and FastAPI backend development",
        "Skilled in MySQL, Redis, Kafka, Distributed Systems and Cloud Computing architecture",
    ]
    b64_pdf = base64.b64encode(_make_ascii_pdf_bytes(lines)).decode("ascii")
    recorded_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        content = json.dumps(
            {"direction": "Python 后端开发", "highlights": ["6 年 Python 经验", "熟悉 FastAPI", "熟悉 MySQL/Redis"]},
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        )

    p_client.app.state.llm_transport = httpx.MockTransport(mock_handler)
    p_client.app.state.settings = dataclasses.replace(p_client.app.state.settings, llm_api_key="test-fake-key")

    legacy_filename = "ZhangSan_Legacy_Resume.pdf"
    payload = {"slot": 2, "name": "旧插件上传", "filename": legacy_filename, "pdf_base64": b64_pdf, "self_name": "Zhang San"}
    resp = p_client.post("/v1/resumes/upload", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    assert legacy_filename not in resp.text
    assert len(recorded_requests) == 1
    assert legacy_filename not in recorded_requests[0].content.decode("utf-8")

    conn = open_db(data_dir)
    try:
        row = conn.execute("SELECT * FROM resume_slots WHERE user_id = 'me' AND slot = 2").fetchone()
        assert row is not None
        assert all(legacy_filename not in str(value) for value in tuple(row))
    finally:
        conn.close()
