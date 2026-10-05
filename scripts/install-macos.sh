#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
# Jet macOS 一键安装与服务配置脚本
# 仅支持 macOS，绝对不使用 sudo
# ==============================================================

echo "[1/7] 检查系统环境..."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[Jet] 错误：本一键安装脚本仅支持 macOS 系统（检测到系统为 $(uname -s)）。"
    echo "Windows/Linux 暂不支持一键安装脚本。"
    exit 1
fi

echo "[2/7] 定位项目根目录并检查完整性..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]] || [[ ! -f "${PROJECT_ROOT}/extension/manifest.json" ]]; then
    echo "[Jet] 错误：在目录 \"${PROJECT_ROOT}\" 下未找到完整的 Jet 项目文件（缺少 pyproject.toml 或 extension/manifest.json）。"
    echo "请确认源码已完整解压，且脚本存放在项目的 scripts 目录下。"
    exit 1
fi

echo "[3/7] 检查 uv 命令行工具..."
if ! command -v uv >/dev/null 2>&1; then
    echo "[Jet] 错误：未检测到 uv 工具（高速 Python 包管理器）。"
    echo "请使用以下官方推荐命令安装 uv 后再运行本脚本（绝不自动安装）："
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "或使用 Homebrew 安装："
    echo "    brew install uv"
    echo "更多说明参见官方文档：https://docs.astral.sh/uv/"
    echo "安装完成后，请关闭并重新打开终端再运行本脚本。"
    exit 1
fi
UV_BIN="$(command -v uv)"
UV_DIR="$(dirname "${UV_BIN}")"

echo "[4/7] 检查端口 47615 与服务归属..."
CURRENT_UID="$(id -u)"
LABEL="local.jet.serve"
SERVICE_TARGET="gui/${CURRENT_UID}/${LABEL}"

if curl -s --max-time 2 http://127.0.0.1:47615/v1/health >/dev/null 2>&1; then
    if ! launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; then
        echo "[Jet] 错误：端口 47615 已被其他服务占用（可能是另一套 Jet 服务或终端前台进程）。"
        echo "本脚本不会修改或停止它。请先确认并停止占用端口 47615 的程序后再重试安装。"
        exit 1
    fi
fi

echo "[5/7] 生成 LaunchAgent 服务配置并校验语法..."
mkdir -p "$HOME/Library/Logs/jet" "$HOME/Library/LaunchAgents"

PLIST_FILE="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_FILE="$HOME/Library/Logs/jet/serve.log"

xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

UV_BIN_XML="$(xml_escape "${UV_BIN}")"
PROJECT_ROOT_XML="$(xml_escape "${PROJECT_ROOT}")"
UV_DIR_XML="$(xml_escape "${UV_DIR}")"
LOG_FILE_XML="$(xml_escape "${LOG_FILE}")"

PLIST_TEMP="${PLIST_FILE}.tmp.$$"
trap 'rm -f "${PLIST_TEMP}"; exit 1' INT TERM

cat > "${PLIST_TEMP}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_BIN_XML}</string>
        <string>run</string>
        <string>--frozen</string>
        <string>jet</string>
        <string>serve</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT_XML}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${UV_DIR_XML}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>${LOG_FILE_XML}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE_XML}</string>
</dict>
</plist>
EOF

if ! plutil -lint "${PLIST_TEMP}" >/dev/null 2>&1; then
    rm -f "${PLIST_TEMP}"
    echo "[Jet] 错误：生成的 LaunchAgent 配置文件语法校验失败（plutil -lint 异常）。"
    exit 1
fi

echo "[6/7] 停止已运行的旧服务..."
if launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; then
    echo "检测到已加载旧版本服务 ${LABEL}，正在安全停止..."
    launchctl bootout "${SERVICE_TARGET}" 2>/dev/null || true
    WAITED=0
    while launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; do
        if [[ ${WAITED} -ge 10 ]]; then
            rm -f "${PLIST_TEMP}"
            echo "[Jet] 错误：旧服务 local.jet.serve 未能停止，已取消安装，现有配置未改动。请稍后重试或重启电脑后再运行本脚本"
            exit 1
        fi
        sleep 1
        WAITED=$((WAITED + 1))
    done
fi

echo "[7/7] 载入启动服务并进行健康检查..."
mv -f "${PLIST_TEMP}" "${PLIST_FILE}"
trap - INT TERM

if ! launchctl bootstrap "gui/${CURRENT_UID}" "${PLIST_FILE}"; then
    echo "[Jet] 错误：服务载入 launchd 失败（launchctl bootstrap 失败）。"
    exit 1
fi

MAX_WAIT=30
if [[ ! -d "${PROJECT_ROOT}/.venv" ]]; then
    echo "首次启动可能需要一两分钟准备环境..."
    MAX_WAIT=180
fi

HEALTH_OK=false
ELAPSED=0
PRINTED_FIRST_RUN_MSG=false

while [[ ${ELAPSED} -lt ${MAX_WAIT} ]]; do
    RESPONSE="$(curl -s --max-time 2 http://127.0.0.1:47615/v1/health || true)"
    if echo "${RESPONSE}" | grep -q '"service"[[:space:]]*:[[:space:]]*"jet"'; then
        HEALTH_OK=true
        break
    fi

    if [[ ${ELAPSED} -ge 10 ]] && [[ "${MAX_WAIT}" -eq 30 ]] && [[ "${PRINTED_FIRST_RUN_MSG}" = false ]]; then
        echo "首次启动可能需要一两分钟准备环境..."
        MAX_WAIT=180
        PRINTED_FIRST_RUN_MSG=true
    fi

    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [[ "${HEALTH_OK}" = false ]]; then
    echo ""
    echo "[Jet] 错误：Jet 服务启动超时（${MAX_WAIT} 秒内未通过健康检查）。"
    echo "服务日志路径：${LOG_FILE}"
    echo "最近 20 行日志如下："
    echo "--------------------------------------------------------------"
    if [[ -f "${LOG_FILE}" ]]; then
        tail -n 20 "${LOG_FILE}" || true
    else
        echo "（未生成日志文件）"
    fi
    echo "--------------------------------------------------------------"
    # 服务设置了「异常退出就每 10 秒重启」，不停掉的话会一直在后台重试、刷日志；
    # 配置文件也一并移除，免得下次登录时又自动启动
    launchctl bootout "${SERVICE_TARGET}" 2>/dev/null || true
    rm -f "${PLIST_FILE}"
    echo "[Jet] 安装未完成：已停止并移除刚载入的后台服务，它不会在后台反复重启。"
    echo "请根据上面的日志排查问题，修好后重新运行本脚本。"
    echo "提示：本脚本只检查 47615 端口；如果你在 .env 里改过 JET_PORT，请先改回或删掉这一行再安装。"
    exit 1
fi

echo ""
echo "=============================================================="
echo "🎉 Jet 本机后台服务已安装并成功启动！"
echo "=============================================================="
echo ""
echo "接下来请完成以下最后三步："
echo ""
echo "1. 加载 Chrome 浏览器插件："
echo "   - 打开 Chrome 浏览器，在地址栏输入：chrome://extensions"
echo "   - 开启右上角「开发者模式」开关"
echo "   - 点击左上角「加载已解压的扩展程序」"
echo "   - 选择此目录：${PROJECT_ROOT}/extension"
echo ""
echo "2. 配对浏览器插件："
echo "   - 在终端进入项目目录运行命令获取配对码："
echo "     cd \"${PROJECT_ROOT}\" && uv run --frozen jet pair"
echo "   - 打开插件设置页（在扩展卡片中点「选项」或点击侧边栏右上角设置图标），填入 6 位配对码完成配对"
echo ""
echo "3. 设置 DeepSeek API Key："
echo "   - 打开 Jet 设置页，在「DeepSeek API Key」区域输入你的 API Key"
echo "   - 点击「测试连接」确认连接成功后点击「保存」"
echo ""
echo "日常服务管理命令："
echo "   - 查看日志：tail -f ~/Library/Logs/jet/serve.log"
echo "   - 卸载服务：bash \"${PROJECT_ROOT}/scripts/uninstall-macos.sh\""
echo "=============================================================="
