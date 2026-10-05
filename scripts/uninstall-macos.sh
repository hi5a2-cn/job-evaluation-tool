#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
# Jet macOS 服务卸载脚本
# 仅支持 macOS，不使用 sudo
# ==============================================================

echo "[1/3] 检查系统环境..."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[Jet] 错误：本卸载脚本仅支持 macOS 系统。"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CURRENT_UID="$(id -u)"
LABEL="local.jet.serve"
SERVICE_TARGET="gui/${CURRENT_UID}/${LABEL}"
PLIST_FILE="$HOME/Library/LaunchAgents/local.jet.serve.plist"

echo "[2/3] 停止并移除后台服务..."
if launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; then
    launchctl bootout "${SERVICE_TARGET}" 2>/dev/null || true
    WAITED=0
    while launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; do
        if [[ ${WAITED} -ge 10 ]]; then
            echo "[Jet] 错误：服务 local.jet.serve 未能停止……配置文件已保留，请稍后重试或重启电脑后再运行本脚本"
            exit 1
        fi
        sleep 1
        WAITED=$((WAITED + 1))
    done
    echo "[Jet] 已停止后台服务 ${LABEL}。"
else
    echo "[Jet] 提示：未检测到正在运行的 ${LABEL} 服务。"
fi

echo "[3/3] 移除配置文件..."
if [[ -f "${PLIST_FILE}" ]]; then
    rm -f "${PLIST_FILE}"
    echo "[Jet] 已移除配置文件 ~/Library/LaunchAgents/local.jet.serve.plist。"
else
    echo "[Jet] 提示：配置文件 ~/Library/LaunchAgents/local.jet.serve.plist 不存在。"
fi

echo ""
echo "=============================================================="
echo "Jet 服务已卸载。"
echo "为防止数据丢失，以下个人数据与日志已保留在你的电脑中："
echo "  - 数据目录：~/Library/Application Support/Jet"
echo "  - 日志目录：~/Library/Logs/jet"
echo "  - 项目目录：${PROJECT_ROOT}"
echo "如需彻底删除数据，可手动删除上述目录。"
echo "=============================================================="
