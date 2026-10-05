"""我的简历 (resume_slots) 与简历建议测试 (FR-001–FR-004, T024–T028)。

覆盖：
1. 静态配置文件已删除且仓库中无真实人名与文件名；
2. resume_slots 领域增删查、Slot/字段/上限校验；
3. resolve_resume_suggestion 解析与异常边界；
4. to_api 的 resume_suggestion 输出及动态解析（用户删除简历后返回 null）；
5. 简历修改绝不影响画像版本、绝不引起可能过时、绝不触发重判；
6. /v1/resumes API 路由鉴权与存取。
"""

from pathlib import Path
import sqlite3
import pytest
from starlette.testclient import TestClient

from jet.api.app import create_app
from jet.config import Settings
from jet.db.store import init_db, open_db, utc_now
from jet.domain.judgements import STALE_METHOD_BASELINE, is_method_changed, to_api
from jet.domain.profiles import save_profile
from jet.domain.resume import (
    ResumeError,
    ResumeInvalidError,
    ResumeSlotInvalidError,
    TooManyResumesError,
    list_resumes,
    resolve_resume_suggestion,
    save_resumes,
)


def test_is_method_changed_uses_stale_baseline():
    """测试 is_method_changed 以过时基线为基准：v9 返回 False，v8 及更早返回 True。"""
    # 1. v9 返回 False（不引起可能过时）
    assert is_method_changed({"source": "llm", "prompt_version": "v9"}) is False

    # 2. v8 及更早版本返回 True（视为判断方式已变更）
    assert is_method_changed({"source": "llm", "prompt_version": "v8"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v7"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v6"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v5"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v4"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v3"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v2"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v1"}) is True


def test_init_db_adds_resume_columns_to_existing_db(tmp_path: Path):
    """测试已有数据库缺少 judgements.resume_direction 与 resume_reason 两列时，init_db 自动补列 (T024)。"""
    # 1. 先初始化一个完整的数据库
    conn = open_db(tmp_path)
    conn.close()

    # 2. 模拟旧库缺少这两列
    conn = sqlite3.connect(str(tmp_path / "jet.db"))
    conn.execute("ALTER TABLE judgements DROP COLUMN resume_direction")
    conn.execute("ALTER TABLE judgements DROP COLUMN resume_reason")
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()}
    assert "resume_direction" not in cols_before
    assert "resume_reason" not in cols_before
    conn.close()

    # 3. 重新调用 init_db 补全结构
    init_db(tmp_path)

    # 4. 验证列已自动补全且为 TEXT 类型
    conn = sqlite3.connect(str(tmp_path / "jet.db"))
    col_info = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()}
    assert "resume_direction" in col_info
    assert col_info["resume_direction"].upper() == "TEXT"
    assert "resume_reason" in col_info
    assert col_info["resume_reason"].upper() == "TEXT"
    conn.close()


def test_save_resumes_slot_persistence_and_validation(data_dir: Path):
    """测试服务端按提交 slot 保存，重复或越界 slot 报错 (FR-001, FR-004)。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 1. 服务端按提交 slot 保存：删除中间一份（例如只保存 slot 1 和 slot 3），保持原编号不重排
    count = save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "运营"},
            {"slot": 3, "name": "简历C", "profile": "算法"},
        ],
    )
    assert count == 2
    resumes = list_resumes(conn, user_id)
    assert len(resumes) == 2
    assert [r["slot"] for r in resumes] == [1, 3]
    assert resumes[0]["name"] == "简历A"
    assert resumes[1]["name"] == "简历C"

    # 2. 重复 slot 报错
    with pytest.raises(ResumeSlotInvalidError, match="重复"):
        save_resumes(
            conn,
            user_id,
            [
                {"slot": 1, "name": "简历1", "profile": "A"},
                {"slot": 1, "name": "简历2", "profile": "B"},
            ],
        )

    # 3. 越界 slot 报错（< 1 或 > 3 或非整数）
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": 0, "name": "简历0", "profile": "A"}])
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": 4, "name": "简历4", "profile": "A"}])
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": -1, "name": "简历负数", "profile": "A"}])
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": "invalid", "name": "简历X", "profile": "A"}])

    conn.close()


def test_resume_slots_db_crud(data_dir: Path):
    """测试 resume_slots 本地数据库增删改查。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 1. 初始为空
    assert list_resumes(conn, user_id) == []

    # 2. 正常保存 2 份简历（虚构名称）
    count = save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "AI 运营、内容运营"},
            {"slot": 2, "name": "简历B", "profile": "数据分析、SQL"},
        ],
    )
    assert count == 2

    resumes = list_resumes(conn, user_id)
    assert len(resumes) == 2
    assert resumes[0]["slot"] == 1
    assert resumes[0]["name"] == "简历A"
    assert resumes[0]["profile"] == "AI 运营、内容运营"
    assert resumes[1]["slot"] == 2
    assert resumes[1]["name"] == "简历B"

    r_map = {r["slot"]: r for r in list_resumes(conn, user_id)}
    assert set(r_map.keys()) == {1, 2}
    assert r_map[1]["name"] == "简历A"

    # 3. 保存 3 份简历（不传 slot 自动分配）
    count3 = save_resumes(
        conn,
        user_id,
        [
            {"name": "简历A", "profile": "运营"},
            {"name": "简历B", "profile": "数据"},
            {"name": "简历C", "profile": "算法"},
        ],
    )
    assert count3 == 3
    resumes3 = list_resumes(conn, user_id)
    assert [r["slot"] for r in resumes3] == [1, 2, 3]
    assert resumes3[2]["name"] == "简历C"

    # 4. 清空简历设置（传空列表）
    count_clear = save_resumes(conn, user_id, [])
    assert count_clear == 0
    assert list_resumes(conn, user_id) == []

    conn.close()


def test_resume_slots_validation_errors(data_dir: Path):
    """测试 resume_slots 保存校验规则：上限 3 份、Slot 合法性、必填与字数限制。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 1. 超过 3 份
    with pytest.raises(TooManyResumesError):
        save_resumes(
            conn,
            user_id,
            [
                {"slot": 1, "name": "简历1", "profile": "A"},
                {"slot": 2, "name": "简历2", "profile": "B"},
                {"slot": 3, "name": "简历3", "profile": "C"},
                {"slot": 4, "name": "简历4", "profile": "D"},
            ],
        )

    # 2. Slot 非法（不是 1, 2, 3）
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": 0, "name": "简历1", "profile": "A"}])
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": 4, "name": "简历1", "profile": "A"}])
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(conn, user_id, [{"slot": "invalid", "name": "简历1", "profile": "A"}])

    # 3. Slot 重复
    with pytest.raises(ResumeSlotInvalidError):
        save_resumes(
            conn,
            user_id,
            [
                {"slot": 1, "name": "简历1", "profile": "A"},
                {"slot": 1, "name": "简历2", "profile": "B"},
            ],
        )

    # 4. 简历名称为空或超长
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "", "profile": "A"}])
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "   ", "profile": "A"}])
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "A" * 101, "profile": "A"}])

    # 5. 适合岗位类型/画像为空或超长（约束 <= 300）
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "简历1", "profile": ""}])
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "简历1", "profile": "   "}])
    with pytest.raises(ResumeInvalidError):
        save_resumes(conn, user_id, [{"slot": 1, "name": "简历1", "profile": "B" * 301}])

    # 6. 入参非列表格式
    with pytest.raises(ResumeError):
        save_resumes(conn, user_id, "invalid_payload")

    conn.close()


def test_resolve_resume_suggestion(data_dir: Path):
    """测试 resolve_resume_suggestion：解析有效 slot 与理由截断、编号无对应简历返回 None。"""
    conn = open_db(data_dir)
    user_id = "me"

    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "运营"},
            {"slot": 2, "name": "简历B", "profile": "数据"},
        ],
    )

    # 1. 正常解析整数 slot
    s1 = resolve_resume_suggestion(conn, user_id, 1, "职责高度匹配运营方向")
    assert s1 == {
        "slot": 1,
        "name": "简历A",
        "reason": "职责高度匹配运营方向",
    }

    # 2. 正常解析字符串 slot ("2" 或 "简历2")
    s2 = resolve_resume_suggestion(conn, user_id, "2", "数据分析匹配")
    assert s2 == {
        "slot": 2,
        "name": "简历B",
        "reason": "数据分析匹配",
    }
    s2_prefix = resolve_resume_suggestion(conn, user_id, "简历2", "数据分析匹配")
    assert s2_prefix == s2

    # 3. 理由超过 40 字截断
    long_reason = "这是一个超过四十个汉字的超长推荐理由测试字符串用于验证截断逻辑是否严格生效并且不会抛出任何异常问题"
    assert len(long_reason) > 40
    s_trunc = resolve_resume_suggestion(conn, user_id, 1, long_reason)
    assert s_trunc is not None
    assert len(s_trunc["reason"]) == 40
    assert s_trunc["reason"] == long_reason[:40]

    # 4. 编号无对应简历（用户只设置了 1、2，没有 3） -> None
    assert resolve_resume_suggestion(conn, user_id, 3, "理由") is None

    # 5. 空理由 / 纯空格理由 -> None
    assert resolve_resume_suggestion(conn, user_id, 1, "") is None
    assert resolve_resume_suggestion(conn, user_id, 1, "   ") is None
    assert resolve_resume_suggestion(conn, user_id, 1, None) is None

    # 6. 非法 slot -> None
    assert resolve_resume_suggestion(conn, user_id, 0, "理由") is None
    assert resolve_resume_suggestion(conn, user_id, 4, "理由") is None
    assert resolve_resume_suggestion(conn, user_id, "invalid", "理由") is None
    assert resolve_resume_suggestion(conn, user_id, None, "理由") is None

    # 7. 缺少 conn 或 user_id -> None
    assert resolve_resume_suggestion(None, user_id, 1, "理由") is None
    assert resolve_resume_suggestion(conn, None, 1, "理由") is None

    conn.close()


def test_to_api_dynamic_resume_suggestion(data_dir: Path):
    """测试 to_api 动态解析 resume_suggestion：
    按当前用户的 resume_slots 解析，编号无对应简历（例如用户后来删了该份简历）则输出 null。
    """
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    # 初始化基础数据
    p_id, _ = save_profile(conn, user_id, {"directions": ["运营"], "cities": ["深圳"]})
    prof = conn.execute("SELECT id FROM profiles WHERE user_id = ?", (user_id,)).fetchone()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (10, 'boss', 'job_dyn_resume', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (100, 10, 1, 'detail', '运营专家', 1, 1, '深圳', 'hash_100', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 100 WHERE id = 10")

    # 用户设置了 2 份简历
    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "运营"},
            {"slot": 2, "name": "简历B", "profile": "数据"},
        ],
    )

    # 插入判断：指向 slot 1
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, resume_direction, resume_reason, rule_result, created_at, finished_at) "
        "VALUES (1001, ?, 10, 100, ?, 'done', 'apply', 'llm', '[]', 'v6', '1', '匹配简历A', '{}', ?, ?)",
        (user_id, prof["id"], now_str, now_str),
    )
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 1001").fetchone()

    # 1. 有对应简历：输出 slot, name, reason
    api_res = to_api(conn, j_row)
    assert api_res["resume_suggestion"] == {
        "slot": 1,
        "name": "简历A",
        "reason": "匹配简历A",
    }

    # 2. 用户更新设置：删除了 slot 1，只保留 slot 2
    save_resumes(
        conn,
        user_id,
        [
            {"slot": 2, "name": "简历B", "profile": "数据"},
        ],
    )
    # 再次读取同一历史判断：slot 1 无对应简历，输出 null
    api_res_deleted = to_api(conn, j_row)
    assert api_res_deleted["resume_suggestion"] is None

    # 3. 旧版本（无 resume_direction / resume_reason 或为 NULL）输出 null
    conn.execute("DELETE FROM judgements WHERE id = 1001")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, resume_direction, resume_reason, rule_result, created_at, finished_at) "
        "VALUES (1002, ?, 10, 100, ?, 'done', 'apply', 'llm', '[]', 'v5', NULL, NULL, '{}', ?, ?)",
        (user_id, prof["id"], now_str, now_str),
    )
    j_v5 = conn.execute("SELECT * FROM judgements WHERE id = 1002").fetchone()
    assert to_api(conn, j_v5)["resume_suggestion"] is None

    conn.close()


def test_resume_changes_do_not_affect_profile_or_staleness(data_dir: Path):
    """测试修改简历设置不得影响画像版本、不得使判断'可能过时'、不得触发自动重判 (FR-005)。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    # 初始画像与简历
    p_id1, changed1 = save_profile(conn, user_id, {"directions": ["后端开发"], "cities": ["深圳"]})
    prof1 = conn.execute("SELECT id, version_no FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    assert prof1["version_no"] == 1

    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "开发"},
            {"slot": 2, "name": "简历B", "profile": "测试"},
        ],
    )

    # 插入已完成判断
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (20, 'boss', 'job_stale_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (200, 20, 1, 'detail', '后端开发专家', 1, 1, '深圳', '描述', 'hash_200', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 200 WHERE id = 20")

    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, resume_direction, resume_reason, rule_result, created_at, finished_at) "
        "VALUES (2001, ?, 20, 200, ?, 'done', 'apply', 'llm', '[]', 'v9', '1', '理由', '{}', ?, ?)",
        (user_id, prof1["id"], now_str, now_str),
    )
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 2001").fetchone()

    # 修改简历设置（新增第 3 份，修改第 1 份画像）
    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A_修改", "profile": "架构设计"},
            {"slot": 2, "name": "简历B", "profile": "测试"},
            {"slot": 3, "name": "简历C", "profile": "数据开发"},
        ],
    )

    # 1. 验证画像版本完全未变
    prof_after = conn.execute("SELECT id, version_no FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    assert prof_after["id"] == prof1["id"]
    assert prof_after["version_no"] == 1

    # 2. 验证判断依然有效，没有变成过时 (stale.profile_changed / method_changed 均为 False)
    api_res = to_api(conn, j_row)
    assert api_res["stale"]["profile_changed"] is False
    assert api_res["stale"]["job_changed"] is False
    assert api_res["stale"]["method_changed"] is False

    # 3. 验证 is_method_changed 保持 False
    assert is_method_changed(j_row) is False

    conn.close()


def test_resumes_api_endpoints(client: TestClient, paired_client: tuple[TestClient, dict[str, str]]):
    """测试 /v1/resumes GET 与 PUT 接口鉴权与数据交互 (T026)。"""
    # 1. 未配对请求 -> 401
    r_unpaired_get = client.get("/v1/resumes")
    assert r_unpaired_get.status_code == 401
    r_unpaired_put = client.put("/v1/resumes", json={"items": []})
    assert r_unpaired_put.status_code == 401

    # 2. 已配对客户端
    p_client, headers = paired_client

    # 3. 配对后初次 GET：空列表
    r_get1 = p_client.get("/v1/resumes", headers=headers)
    assert r_get1.status_code == 200
    assert r_get1.json() == []

    # 4. PUT 保存 2 份简历
    put_payload = {
        "items": [
            {"slot": 1, "name": "简历A", "profile": "AI 运营"},
            {"slot": 2, "name": "简历B", "profile": "数据分析"},
        ]
    }
    r_put = p_client.put("/v1/resumes", json=put_payload, headers=headers)
    assert r_put.status_code == 200
    assert r_put.json() == {"ok": True, "count": 2}

    # 5. 再次 GET 校验
    r_get2 = p_client.get("/v1/resumes", headers=headers)
    assert r_get2.status_code == 200
    get_data = r_get2.json()
    assert len(get_data) == 2
    assert get_data[0]["slot"] == 1
    assert get_data[0]["name"] == "简历A"
    assert get_data[0]["profile"] == "AI 运营"
    assert get_data[1]["slot"] == 2
    assert get_data[1]["name"] == "简历B"
    assert get_data[1]["profile"] == "数据分析"

    # 6. 非法 PUT：超过 3 份
    invalid_payload = {
        "items": [
            {"slot": 1, "name": "A", "profile": "a"},
            {"slot": 2, "name": "B", "profile": "b"},
            {"slot": 3, "name": "C", "profile": "c"},
            {"slot": 4, "name": "D", "profile": "d"},
        ]
    }
    r_invalid = p_client.put("/v1/resumes", json=invalid_payload, headers=headers)
    assert r_invalid.status_code == 422
    assert r_invalid.json()["error"] == "too_many_resumes"

    # 7. 非法 PUT：名称为空
    empty_name_payload = {
        "items": [
            {"slot": 1, "name": "  ", "profile": "a"},
        ]
    }
    r_empty_name = p_client.put("/v1/resumes", json=empty_name_payload, headers=headers)
    assert r_empty_name.status_code == 422
    assert r_empty_name.json()["error"] == "resume_invalid"
