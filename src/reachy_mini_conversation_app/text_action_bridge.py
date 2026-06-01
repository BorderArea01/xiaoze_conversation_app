"""Local action bridge for typed chat commands."""

from __future__ import annotations
import re
import json
from typing import Any
from dataclasses import dataclass

from reachy_mini_conversation_app.tools.dance import AVAILABLE_MOVES, DANCE_AVAILABLE
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies, dispatch_tool_call
from reachy_mini_conversation_app.tools.play_emotion import RECORDED_MOVES, EMOTION_AVAILABLE


@dataclass(frozen=True)
class TextAction:
    """A local robot action parsed from typed text."""

    tool_name: str
    args: dict[str, Any]
    label: str


EMOTION_ALIASES = {
    "开心": "cheerful1",
    "高兴": "cheerful1",
    "快乐": "cheerful1",
    "好奇": "curious1",
    "疑惑": "confused1",
    "困惑": "confused1",
    "惊讶": "amazed1",
    "震惊": "amazed1",
    "无聊": "boredom1",
    "焦虑": "anxiety1",
    "紧张": "anxiety1",
    "专注": "attentive1",
    "注意": "attentive1",
    "安抚": "calming1",
    "冷静": "calming1",
    "厌恶": "disgusted1",
    "嫌弃": "disgusted1",
    "失落": "downcast1",
    "难过": "downcast1",
    "不开心": "downcast1",
}

HEAD_DIRECTIONS = {
    "左": "left",
    "左边": "left",
    "向左": "left",
    "右": "right",
    "右边": "right",
    "向右": "right",
    "上": "up",
    "抬头": "up",
    "向上": "up",
    "下": "down",
    "低头": "down",
    "向下": "down",
    "前": "front",
    "前方": "front",
    "看前面": "front",
    "回正": "front",
}


def _available_emotions() -> list[str]:
    if not EMOTION_AVAILABLE or RECORDED_MOVES is None:
        return []
    try:
        return list(RECORDED_MOVES.list_moves())
    except Exception:
        return []


def _contains_name(text: str, name: str) -> bool:
    """Return true when a tool enum name appears as a standalone-ish token."""
    escaped = re.escape(name.lower())
    return re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", text) is not None


def parse_text_actions(query: str) -> list[TextAction]:
    """Parse typed text into local robot actions.

    Exact enum names are preferred so all installed emotions and dances are
    addressable without maintaining a separate hard-coded list.
    """
    text = (query or "").strip()
    text_l = text.lower()
    if not text_l:
        return []

    actions: list[TextAction] = []

    if any(key in text for key in ("停止跳舞", "停止舞蹈", "停下舞蹈", "别跳了", "停止 dance")):
        actions.append(TextAction("stop_dance", {}, "停止舞蹈"))
    if any(key in text for key in ("停止表情", "停止情绪", "停止 mood", "停止 emotion")):
        actions.append(TextAction("stop_emotion", {}, "停止表情"))

    if any(key in text for key in ("开启头部跟踪", "开始头部跟踪", "开启头部追踪", "开始头部追踪")):
        actions.append(TextAction("head_tracking", {"start": True}, "开启头部跟踪"))
    elif any(key in text for key in ("关闭头部跟踪", "停止头部跟踪", "关闭头部追踪", "停止头部追踪")):
        actions.append(TextAction("head_tracking", {"start": False}, "关闭头部跟踪"))

    if any(key in text for key in ("看", "转头", "抬头", "低头", "回正")):
        for keyword, direction in HEAD_DIRECTIONS.items():
            if keyword in text:
                actions.append(TextAction("move_head", {"direction": direction}, f"看{keyword}"))
                break

    dance_names = list(AVAILABLE_MOVES.keys()) if DANCE_AVAILABLE else []
    for name in dance_names:
        if _contains_name(text_l, name):
            actions.append(TextAction("dance", {"move": name}, f"舞蹈 {name}"))
            break
    else:
        if any(key in text_l for key in ("跳舞", "舞蹈", "dance")):
            actions.append(TextAction("dance", {}, "随机舞蹈"))

    emotion_names = _available_emotions()
    for name in emotion_names:
        if _contains_name(text_l, name):
            actions.append(TextAction("play_emotion", {"emotion": name}, f"表情 {name}"))
            break
    else:
        for keyword, emotion in EMOTION_ALIASES.items():
            if keyword in text and emotion in emotion_names:
                actions.append(TextAction("play_emotion", {"emotion": emotion}, f"表情 {emotion}"))
                break

    return actions


def is_action_only_query(query: str, actions: list[TextAction] | None = None) -> bool:
    """Return whether the text is meant as a local robot action only."""
    text = (query or "").strip().lower()
    if not text:
        return False
    parsed_actions = actions if actions is not None else parse_text_actions(query)
    if not parsed_actions:
        return False

    compact = re.sub(r"[\s,，。.!！?？、；;:：\"'“”‘’（）()]+", "", text)
    if any(word in compact for word in ("查询", "查一下", "帮我", "告诉我", "介绍", "解释")):
        return False
    if compact in {"跳舞", "舞蹈", "dance", "跳个舞", "跳一段", "跳支舞"}:
        return True

    action_words = (
        "跳舞",
        "舞蹈",
        "dance",
        "看左",
        "看右",
        "看上",
        "看下",
        "抬头",
        "低头",
        "回正",
        "开启头部跟踪",
        "关闭头部跟踪",
        "停止舞蹈",
        "停止表情",
        "停止跳舞",
    )
    if any(word in compact for word in action_words) and len(compact) <= 12:
        return True

    for action in parsed_actions:
        for value in action.args.values():
            if isinstance(value, str) and compact == value.lower():
                return True
    return False


async def execute_text_actions(query: str, deps: ToolDependencies) -> list[dict[str, Any]]:
    """Execute local robot actions parsed from typed text."""
    results: list[dict[str, Any]] = []
    for action in parse_text_actions(query):
        result = await dispatch_tool_call(action.tool_name, json.dumps(action.args), deps)
        results.append(
            {
                "tool": action.tool_name,
                "label": action.label,
                "args": action.args,
                "result": result,
            }
        )
    return results


def format_action_results(results: list[dict[str, Any]]) -> str:
    """Format action execution results for the typed chat panel."""
    if not results:
        return ""
    lines = ["本地动作："]
    for item in results:
        result = item.get("result") or {}
        if isinstance(result, dict) and result.get("error"):
            lines.append(f"- {item.get('label')}: 失败，{result['error']}")
        else:
            lines.append(f"- {item.get('label')}: 已执行")
    return "\n".join(lines)
