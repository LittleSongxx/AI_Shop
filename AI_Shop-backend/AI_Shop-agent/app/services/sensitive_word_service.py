from app.services.redis_service import redis_service


class SensitiveWordService:

    async def replace(self, text: str) -> str:

        words = await redis_service.get_sensitive_words()
        if not words or not text:
            return text
        result = text
        for item in words:

            word = item.get("word") or item.get("Word")
            replace = item.get("replaceWord") or item.get("replace_word") or "***"
            if word and word in result:
                result = result.replace(word, replace)
        return result

sensitive_word_service = SensitiveWordService()
