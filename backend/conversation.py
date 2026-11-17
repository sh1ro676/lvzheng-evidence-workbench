"""Bounded conversation context shared by HTTP and model clients."""
from __future__ import annotations

import re

MAX_MESSAGES = 12
MAX_MESSAGE_CHARS = 6000
MAX_HISTORY_CHARS = 24000
HISTORY_RULE = (
    "此前对话仅用于理解追问和指代，不是本次已核实的资料。"
    "历史回答可能有误，历史证据编号不属于本次证据包，不得直接沿用。"
)


def normalize_history(value: object = None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("history 必须是消息数组")
    for item in value:
        if (not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
                or not isinstance(item.get("content"), str)):
            raise ValueError("历史消息仅支持 user/assistant 和文本 content")
    history = []
    remaining = MAX_HISTORY_CHARS
    for item in reversed(value[-MAX_MESSAGES:]):
        content = item["content"].strip()[:min(MAX_MESSAGE_CHARS, remaining)]
        # Old turn-local numbers must never look like current citations.
        content = re.sub(r"【证据\s*\d+\s*】", "[历史引用]", content)
        if content:
            history.append({"role": item["role"], "content": content})
            remaining -= len(content)
        if remaining <= 0:
            break
    history.reverse()
    while history and history[0]["role"] == "assistant":
        history.pop(0)
    return history
