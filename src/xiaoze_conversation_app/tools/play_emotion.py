import random
import logging
from typing import Any, Dict, Optional

from xiaoze_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# Lazy-loaded emotion library
_RECORDED_MOVES: Any = None
_EMOTION_LIBRARY_ERROR: Optional[str] = None


def _get_recorded_moves():
    """Lazily initialize the emotion library on first use."""
    global _RECORDED_MOVES, _EMOTION_LIBRARY_ERROR
    if _RECORDED_MOVES is not None:
        return _RECORDED_MOVES
    if _EMOTION_LIBRARY_ERROR is not None:
        return None
    try:
        from reachy_mini.motion.recorded_move import RecordedMoves
        from xiaoze_conversation_app.dance_emotion_moves import EmotionQueueMove

        _RECORDED_MOVES = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
        return _RECORDED_MOVES
    except ImportError as e:
        _EMOTION_LIBRARY_ERROR = f"Emotion library not available: {e}"
        logger.warning(_EMOTION_LIBRARY_ERROR)
        return None
    except Exception as e:
        _EMOTION_LIBRARY_ERROR = f"Emotion library init failed: {e}"
        logger.warning(_EMOTION_LIBRARY_ERROR)
        return None


def get_available_emotions_and_descriptions() -> str:
    """Get formatted list of available emotions with descriptions."""
    rm = _get_recorded_moves()
    if rm is None:
        return "Emotions not available"

    try:
        emotion_names = rm.list_moves()
        if not emotion_names:
            return "No emotions currently available"

        output = "Available emotions:\n"
        for name in emotion_names:
            description = rm.get(name).description
            output += f" - {name}: {description}\n"
        return output
    except Exception as e:
        return f"Error getting emotions: {e}"


class PlayEmotion(Tool):
    """Play a pre-recorded emotion."""

    name = "play_emotion"
    description = "Play a pre-recorded emotion"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        rm = _get_recorded_moves()
        emotion_names = rm.list_moves() if rm is not None else []
        return {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "enum": emotion_names,
                    "description": f"""Name of the emotion to play; omit for random.
                                    Here is a list of the available emotions, you MUST only choose from these: \n
                                    {get_available_emotions_and_descriptions() if emotion_names else '(none available)'}
                                    """,
                },
            },
            "required": [],
        }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Play a pre-recorded emotion."""
        rm = _get_recorded_moves()
        if rm is None:
            return {"error": "Emotion system not available"}

        emotion_name = kwargs.get("emotion")

        logger.info("Tool call: play_emotion emotion=%s", emotion_name)

        try:
            emotion_names = rm.list_moves()
            if not emotion_names:
                return {"error": "No emotions currently available"}

            if not emotion_name:
                emotion_name = random.choice(emotion_names)

            if emotion_name not in emotion_names:
                return {"error": f"Unknown emotion '{emotion_name}'. Available: {emotion_names}"}

            from xiaoze_conversation_app.dance_emotion_moves import EmotionQueueMove

            movement_manager = deps.movement_manager
            emotion_move = EmotionQueueMove(emotion_name, rm)
            movement_manager.queue_move(emotion_move)

            return {"status": "queued", "emotion": emotion_name}

        except Exception as e:
            logger.exception("Failed to play emotion")
            return {"error": f"Failed to play emotion: {e!s}"}
