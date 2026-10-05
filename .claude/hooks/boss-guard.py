#!/usr/bin/env python3
"""Guardrail: ask before any browser tool or shell command opens a BOSS Zhipin page.

Opening a BOSS conversation marks it read (visible to HR) and can trigger risk
controls, so CLAUDE.md requires the user's explicit consent. This hook turns
that rule into an approval prompt. It sees URLs passed to the tool; it cannot
see a BOSS tab that is already open, so CLAUDE.md still applies to clicks.
"""

import json
import sys

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

if "zhipin.com" in json.dumps(event.get("tool_input") or {}).lower():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "要打开 BOSS 直聘（zhipin.com）页面。打开对话会被 HR 看到已读，"
                                        "也可能触发风控。只有用户明确同意才继续；优先用本地数据库或 /v1/chat/preview。",
        }
    }, ensure_ascii=False))
