import re

from app.utils.biz_payload import strip_embedded_product_json

ACT_TOKEN_PATTERN = re.compile(r"【act_[a-f0-9]{32}】", re.I)

FALSE_COMPLETION_PATTERNS = [
    r"退款已成功",
    r"已为您确认收货",
    r"评价已提交",
    r"追评已完成",
]

FALSE_CAPABILITY_PATTERNS = [
    r"帮你处理下单",
    r"帮你下单",
    r"我来帮你下单",
    r"帮你完成下单",
    r"提供.{0,12}收货地址",
    r"提供.{0,12}联系方式",
    r"收集.{0,8}地址",
    r"处理下单事宜",
    r"收货地址和联系方式",
]

_ORDER_CAPABILITY_HINT = "如需购买，请点击商品详情页或购物车自行完成下单。"

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]+",
    flags=re.UNICODE,
)

_MULTI_SPACE = re.compile(r"[ \t]{2,}")

def strip_emojis(text: str | None) -> str:

    if not text:
        return ""
    cleaned = _EMOJI_PATTERN.sub("", text)
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    return cleaned.strip()

class OutputGuardrail:

    def extract_action_tokens(self, text: str) -> list[str]:

        return ACT_TOKEN_PATTERN.findall(text)

    def validate_no_false_completion(self, text: str, tools_called: list[str]) -> str:

        propose_called = any(t.startswith("PROPOSE_") for t in tools_called)
        for pattern in FALSE_COMPLETION_PATTERNS:
            if re.search(pattern, text) and not propose_called:

                return strip_emojis(text) + "\n\n（请在下方确认卡片中完成操作）"

        return self.validate_no_false_capability(text, tools_called)

    def validate_no_false_capability(self, text: str, tools_called: list[str]) -> str:

        cleaned = strip_emojis(text)
        if any(re.search(p, cleaned) for p in FALSE_CAPABILITY_PATTERNS):
            if _ORDER_CAPABILITY_HINT not in cleaned:
                return cleaned.rstrip() + f"\n\n（{_ORDER_CAPABILITY_HINT}）"
        return cleaned

    def validate_chunk(self, chunk: str) -> str:

        return strip_embedded_product_json(strip_emojis(chunk))
