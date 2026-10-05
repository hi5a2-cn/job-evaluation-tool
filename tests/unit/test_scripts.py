import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
INSTALL_SCRIPT = SCRIPTS_DIR / "install-macos.sh"
UNINSTALL_SCRIPT = SCRIPTS_DIR / "uninstall-macos.sh"


def test_scripts_exist():
    assert INSTALL_SCRIPT.is_file(), f"{INSTALL_SCRIPT} does not exist"
    assert UNINSTALL_SCRIPT.is_file(), f"{UNINSTALL_SCRIPT} does not exist"


def test_bash_n_syntax():
    """Verify bash -n syntax validity for both scripts."""
    for script in (INSTALL_SCRIPT, UNINSTALL_SCRIPT):
        res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert res.returncode == 0, f"bash -n failed for {script.name}:\n{res.stderr}"


def test_no_sudo_and_strict_pipefail():
    """Both scripts must use set -euo pipefail and never invoke sudo."""
    for script in (INSTALL_SCRIPT, UNINSTALL_SCRIPT):
        content = script.read_text(encoding="utf-8")
        assert "set -euo pipefail" in content
        # Ensure sudo command is never executed
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                assert not stripped.startswith("sudo "), f"Unexpected sudo call in {script.name}: {line}"
                assert " sudo " not in stripped, f"Unexpected sudo call in {script.name}: {line}"


def test_isolated_label_contract():
    """Scripts must strictly use local.jet.serve and no other labels."""
    all_labels = set()
    for script in (INSTALL_SCRIPT, UNINSTALL_SCRIPT):
        content = script.read_text(encoding="utf-8")
        assert "local.jet.serve" in content

        label_matches = re.findall(r'LABEL=["\']?([^"\'\s]+)["\']?', content)
        plist_matches = re.findall(
            r"(?:<key>\s*Label\s*</key>|<Label>.*?</Label>|<Label>)\s*<string>([^<]+)</string>",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        gui_matches = re.findall(r'gui/[^/\s"\']+/([^/\s"\']+)', content)

        label_var = label_matches[0] if label_matches else None
        script_labels = set()
        for item in label_matches + plist_matches + gui_matches:
            if item in ("${LABEL}", "$LABEL") and label_var:
                script_labels.add(label_var)
            else:
                script_labels.add(item)

        assert script_labels == {"local.jet.serve"}
        all_labels.update(script_labels)

    assert all_labels == {"local.jet.serve"}


def test_install_script_contracts():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")
    # Check Darwin OS check
    assert "Darwin" in content
    # Check project integrity
    assert "pyproject.toml" in content
    assert "extension/manifest.json" in content
    # Check uv instructions
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in content
    assert "brew install uv" in content
    # Check 47615 port conflict protection
    assert "47615" in content
    assert "/v1/health" in content
    # Check XML escaping and plutil lint
    assert "xml_escape" in content
    assert "plutil -lint" in content
    # Check LaunchAgent parameters
    assert "ProgramArguments" in content
    assert "WorkingDirectory" in content
    assert "EnvironmentVariables" in content
    assert "KeepAlive" in content
    assert "ThrottleInterval" in content
    # Check launchctl bootstrap
    assert "launchctl bootstrap" in content
    # Check health check timeouts (30s and 180s)
    assert "180" in content
    # Check next steps instructions
    assert "chrome://extensions" in content
    assert "jet pair" in content
    assert "DeepSeek API Key" in content
    # Check failure log tail
    assert "tail -n 20" in content


def test_uninstall_script_contracts():
    content = UNINSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "Darwin" in content
    assert "launchctl bootout" in content
    assert "local.jet.serve.plist" in content
    # Retention notices for data and logs
    assert "Application Support/Jet" in content
    assert "Library/Logs/jet" in content


def test_uninstall_plist_removal_after_service_unloaded():
    """Ensure plist deletion is located after confirming the service is unloaded."""
    content = UNINSTALL_SCRIPT.read_text(encoding="utf-8")
    timeout_err = "[Jet] 错误：服务 local.jet.serve 未能停止……配置文件已保留，请稍后重试或重启电脑后再运行本脚本"
    assert timeout_err in content

    timeout_idx = content.find(timeout_err)
    stopped_msg_idx = content.find("已停止后台服务")
    rm_plist_idx = content.find('rm -f "${PLIST_FILE}"')

    assert timeout_idx != -1
    assert stopped_msg_idx != -1
    assert rm_plist_idx != -1
    assert timeout_idx < stopped_msg_idx < rm_plist_idx
    assert "exit 1" in content[timeout_idx:stopped_msg_idx]


def test_install_script_xml_escape():
    """Verify xml_escape in install-macos.sh properly escapes XML entities via bash."""
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")
    start = content.find("xml_escape()")
    assert start != -1, "xml_escape function definition not found in install-macos.sh"
    end = content.find("}", start) + 1
    func_def = content[start:end]

    cmd = f"{func_def}\nxml_escape 'a&b<c>d'"
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0, f"xml_escape invocation failed: {res.stderr}"
    assert res.stdout.strip() == "a&amp;b&lt;c&gt;d"


def test_install_script_step6_timeout_exit():
    """Static check to ensure step 6 timeout branch in install-macos.sh deletes temp plist and exits 1."""
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")
    step6_idx = content.find("[6/7]")
    step7_idx = content.find("[7/7]")
    assert step6_idx != -1, "[6/7] step marker not found"
    assert step7_idx != -1, "[7/7] step marker not found"
    step6_content = content[step6_idx:step7_idx]

    timeout_err = "[Jet] 错误：旧服务 local.jet.serve 未能停止，已取消安装，现有配置未改动。请稍后重试或重启电脑后再运行本脚本"
    assert timeout_err in step6_content
    assert 'rm -f "${PLIST_TEMP}"' in step6_content

    err_idx = step6_content.find(timeout_err)
    assert "exit 1" in step6_content[err_idx:]
