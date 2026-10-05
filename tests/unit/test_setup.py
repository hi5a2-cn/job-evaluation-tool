import os
from pathlib import Path
import pytest

import jet
from jet.cli import main
from jet.db.store import open_db


def test_version():
    assert jet.__version__ == "0.1.0"


def test_cli_version(capsys: pytest.CaptureFixture[str]):
    ret = main(["--version"])
    assert ret == 0
    captured = capsys.readouterr()
    # Argparse may output version to stdout or stderr depending on python version
    out = captured.out + captured.err
    assert "0.1.0" in out


def test_data_dir_fixture(data_dir: Path):
    assert data_dir.name == "jet-data"
    assert os.environ.get("JET_DATA_DIR") == str(data_dir)


def test_cli_pair_not_running(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    ret = main(["pair", "--data-dir", str(data_dir)])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Jet 未运行，请先运行 jet serve" in captured.out


def test_cli_stats_no_data(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    ret = main(["stats", "--data-dir", str(data_dir)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "还没有数据" in captured.out


def test_cli_stats_with_data(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    conn = open_db(data_dir)
    conn.close()

    ret = main(["stats", "--data-dir", str(data_dir)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "岗位总数" in captured.out
    assert "今日大模型调用" in captured.out
