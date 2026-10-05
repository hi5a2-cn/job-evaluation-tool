"""体检第 24 条：跨多个版本升级时，提示里的备份必须是升级前的原始备份。"""

import sqlite3
from pathlib import Path

from jet.db.migrations import LATEST_VERSION
from jet.db.store import init_db
from tests.unit.test_migrations import _setup_v0_db


def test_multi_step_migration_reports_original_backup(tmp_path: Path):
    data_dir = tmp_path / "mig_multi"
    data_dir.mkdir()
    _setup_v0_db(data_dir / "jet.db")

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 0
    assert res.to_version == LATEST_VERSION

    # 每一步都留下一份备份；提示的必须是第一份（v0，升级前的数据）
    assert len(list(data_dir.glob("jet.db.bak-*"))) > 1
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v0")

    backup = sqlite3.connect(str(data_dir / res.backup_filename))
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 3
    finally:
        backup.close()
