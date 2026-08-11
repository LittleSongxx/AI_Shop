from __future__ import annotations

import re

_NON_PRODUCT_BAG_PREFIXES = (
    "安装",
    "软件",
    "数据",
    "文件",
    "压缩",
    "流量",
    "服务",
    "工具",
    "代码",
    "表情",
    "套餐",
    "红包",
    "承包",
)
_NON_PRODUCT_BAG_SUFFIXES = (
    "括",
    "含",
    "装",
    "邮",
    "裹",
    "年",
    "月",
    "退",
    "换",
    "赔",
    "税",
    "办",
    "容",
    "子",
)
_BARE_BAG_SHOPPING_CONTEXT_RE = re.compile(
    r"(?:买|找|选|挑|推荐|想要|想买|需要|看看|同款|类似|适合)"
    r"[^，。！？,.!?]{0,24}?包"
    r"(?=$|[\s，。！？,.!?]|预算|价格|价位|用于|适合|主要|要求|颜色|材质|容量|对)"
)


def has_bare_bag_category(text: str | None) -> bool:
    """Recognize a bag as a shopping noun without matching package phrases."""
    value = str(text or "")
    if not _BARE_BAG_SHOPPING_CONTEXT_RE.search(value):
        return False
    for match in re.finditer("包", value):
        index = match.start()
        prefix = value[max(0, index - 4) : index]
        suffix = value[index + 1 : index + 2]
        if any(prefix.endswith(candidate) for candidate in _NON_PRODUCT_BAG_PREFIXES):
            continue
        if any(suffix.startswith(candidate) for candidate in _NON_PRODUCT_BAG_SUFFIXES):
            continue
        local = value[max(0, index - 28) : index + 12]
        if _BARE_BAG_SHOPPING_CONTEXT_RE.search(local):
            return True
    return False
