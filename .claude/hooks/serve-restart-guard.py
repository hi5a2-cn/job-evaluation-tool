#!/usr/bin/env python3
"""Guardrail: ask before starting or restarting the jet service.

A restart is what makes changes to content sent to external models go live,
and CLAUDE.md requires the user's consent first (FR-057, 2026-09-28). This
hook asks on every start or restart, whatever changed.
"""

import json
import re
import sys

SERVE = re.compile(r"""\bjet["']?\s+["']?serve\b|local\.jet\.serve|install-macos\.sh""")

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

command = (event.get("tool_input") or {}).get("command") or ""
if SERVE.search(command):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "要启动或重启 jet 服务。如果这次改动涉及发给外部模型的内容"
                                        "（新增字段、改 prompt），请先向用户列出具体字段并取得同意。",
        }
    }, ensure_ascii=False))
