"""Deterministic separation of a legitimate query from an explicit attack suffix."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EXPLICIT_APPENDIX = re.compile(
    r"(?P<prefix>.+?)(?:[；;。.!?！？\n]\s*)"
    r"(?P<attack>(?:附加|额外)(?:命令|指令)\s*[:：].+)$",
    re.I | re.S,
)
_EXPLICIT_OVERRIDE = re.compile(
    r"(?P<prefix>.+?)(?:[；;。.!?！？\n]\s*)"
    # A natural-language connector often sits between the business question
    # and the attack appendix ("然后忽略...", "随后请无视...").  Keep the
    # connector inside the locked suffix so the legitimate prefix is retained.
    r"(?P<attack>(?:(?:然后|随后|接着|再|并且|并)\s*)?(?:请)?"
    r"(?:忽略|无视|绕过)[^。；\n]{0,20}(?:规则|指令|知识库|提示词)"
    r"[^。；\n]{0,12}(?:并|然后|再).+)$",
    re.I | re.S,
)
_META_ONLY_PREFIX = re.compile(
    r"^(?:系统|开发者|助手|assistant|system|developer)(?:消息|指令|命令)?\s*[:：]",
    re.I,
)


@dataclass(frozen=True)
class QuerySeparation:
    safe_query: str
    security_flags: tuple[str, ...] = ()
    separated: bool = False


def separate_explicit_attack_suffix(text: str | None) -> QuerySeparation:
    """Remove only a locked, explicit attack appendix after a usable question.

    Ambiguous wording is intentionally left unchanged. Pure attacks and meta-role
    messages have no legitimate prefix and remain available to the normal input
    guard, which rejects them.
    """

    value = str(text or "").strip()
    if not value:
        return QuerySeparation("")
    for rule_id, pattern in (
        ("mixed_injection_explicit_appendix", _EXPLICIT_APPENDIX),
        ("mixed_injection_explicit_override", _EXPLICIT_OVERRIDE),
    ):
        match = pattern.fullmatch(value)
        if not match:
            continue
        prefix = match.group("prefix").strip(" \t\r\n；;。.!?！？")
        if len(prefix) < 3 or _META_ONLY_PREFIX.match(prefix):
            continue
        return QuerySeparation(prefix, (rule_id,), True)
    return QuerySeparation(value)
