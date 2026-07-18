import re

from app.config.settings import get_settings

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"忽略.*指令",
    r"system\s*prompt",
]

class InputGuardrail:

    def __init__(self, sensitive_words: list[str] | None = None):

        self._sensitive_words = sensitive_words or []

    def filter_sensitive(self, text: str) -> str:

        result = text
        for word in self._sensitive_words:
            if word:
                result = result.replace(word, "*" * len(word))
        return result

    def detect_injection(self, text: str) -> bool:

        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def check_chat_limit(self, user_round_count: int) -> bool:

        settings = get_settings()
        if settings.ai_chat_limit <= 0:
            return True
        return user_round_count < settings.ai_chat_limit
