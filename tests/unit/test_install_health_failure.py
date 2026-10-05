"""体检第 23 条：安装时健康检查失败，脚本要停掉并移除刚载入的后台服务。

在临时目录里运行安装脚本的副本，launchctl、curl、uv、plutil、sleep、uname 全部换成假的，
HOME 指向临时目录，不会碰到本机真实的 launchd 和 ~/Library。
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-macos.sh"

FAKES = {
    "uname": 'echo Darwin\n',
    "uv": 'exit 0\n',
    "plutil": 'exit 0\n',
    "sleep": 'exit 0\n',
    "curl": (
        # 只有服务已载入（bootstrap 之后）才可能回应健康检查
        'if [ "${FAKE_HEALTH:-}" = ok ] && [ -f "$FAKE_LAUNCHCTL_STATE" ]; then\n'
        '  echo \'{"service":"jet"}\'; exit 0\n'
        'fi\n'
        'exit 7\n'
    ),
    "launchctl": (
        'echo "$*" >> "$FAKE_LAUNCHCTL_LOG"\n'
        'case "$1" in\n'
        '  print) [ -f "$FAKE_LAUNCHCTL_STATE" ] && exit 0; exit 1 ;;\n'
        '  bootstrap) touch "$FAKE_LAUNCHCTL_STATE"; exit 0 ;;\n'
        '  bootout) rm -f "$FAKE_LAUNCHCTL_STATE"; exit 0 ;;\n'
        'esac\n'
        'exit 0\n'
    ),
}


def _run_install(tmp_path: Path, health: str) -> tuple[subprocess.CompletedProcess, Path, Path, Path]:
    project = tmp_path / "proj"
    (project / "scripts").mkdir(parents=True)
    (project / "extension").mkdir()
    (project / "pyproject.toml").write_text("", encoding="utf-8")
    (project / "extension" / "manifest.json").write_text("{}", encoding="utf-8")
    script = project / "scripts" / "install-macos.sh"
    shutil.copy(INSTALL_SCRIPT, script)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for name, body in FAKES.items():
        f = fakebin / name
        f.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        f.chmod(f.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "launchctl.log"
    path = f"{fakebin}:/usr/bin:/bin"
    # 安全保护：确认运行时找到的一定是假的 launchctl，绝不能调用真的
    assert shutil.which("launchctl", path=path) == str(fakebin / "launchctl")

    env = {
        "PATH": path,
        "HOME": str(home),
        "FAKE_HEALTH": health,
        "FAKE_LAUNCHCTL_LOG": str(log),
        "FAKE_LAUNCHCTL_STATE": str(tmp_path / "loaded"),
    }
    res = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    plist = home / "Library" / "LaunchAgents" / "local.jet.serve.plist"
    return res, log, plist, tmp_path / "loaded"


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")
def test_health_check_failure_unloads_and_removes_service(tmp_path: Path):
    res, log, plist, loaded = _run_install(tmp_path, health="fail")

    assert res.returncode == 1, res.stdout + res.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    uid = os.getuid()
    bootstrap_idx = next(i for i, c in enumerate(calls) if c.startswith("bootstrap "))
    assert f"bootout gui/{uid}/local.jet.serve" in calls[bootstrap_idx + 1:]
    assert not loaded.exists()
    assert not plist.exists()
    assert "安装未完成" in res.stdout
    assert "重新运行本脚本" in res.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")
def test_health_check_success_keeps_service(tmp_path: Path):
    res, log, plist, loaded = _run_install(tmp_path, health="ok")

    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not any(c.startswith("bootout ") for c in calls)
    assert loaded.exists()
    assert plist.exists()
    assert "安装未完成" not in res.stdout
