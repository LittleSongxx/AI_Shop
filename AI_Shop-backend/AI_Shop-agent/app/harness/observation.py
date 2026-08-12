"""工具结果进入模型上下文和展示链路前的 Observation 层。

ReAct 里模型看到的是 ``ToolMessage.content``——工具实现的任何输出都从这里进入
上下文，包括未来的新工具。与其在每个工具实现里各自治理，不如在入口统一把关：
脱敏（PII 不进上下文）→ 裁剪（长度受限）→ 污染检疫 → 记录治理痕迹。

调用方必须同时把治理结果用于 ``ToolMessage``、``chunks`` 和 ``search_hint``；
如果结构化卡片或业务载荷命中污染规则，则整次工具结果都不可用于展示或推理。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.harness.guardrails.channel_guard import scan_external_content

# 手机号：1 开头 11 位；用前后断言避免把长数字串中间的片段误伤。
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
# 邮箱：标准字符集。
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# 身份证：17 位数字 + 1 位数字/X/x。
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

DEFAULT_MAX_CHARS = 4000
CONTAMINATED_CONTENT_PLACEHOLDER = "[外部工具内容已因安全策略隔离]"


@dataclass
class ToolObservation:
    """一次工具结果治理的产物：模型看到的文本 + 治理痕迹。

    ``contaminated`` / ``matched_rules`` 是 A2 通道扫描的产物。命中注入话术
    时 ``text`` 会被替换为固定占位文本，调用方还必须丢弃同一结果携带的卡片、
    ``biz_data`` 和检索提示，避免污染内容从展示侧绕过模型上下文治理。
    """

    text: str
    original_len: int
    truncated: bool = False
    redacted_count: int = 0
    omitted_fields: list[str] = field(default_factory=list)
    contaminated: bool = False
    matched_rules: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        # 字段语义：originalLength=脱敏前原文长度；observedLength=模型实际看到的
        # 文本长度（污染时即占位符长度）；truncated/redactedCount/contaminated
        # 三类损失各自独立标记，消费方按组合还原，勿把 observedLength 的缺口
        # 全部算作截断损失。
        return {
            "originalLength": self.original_len,
            "observedLength": len(self.text),
            "truncated": self.truncated,
            "redactedCount": self.redacted_count,
            "omittedFields": self.omitted_fields,
            "contaminated": self.contaminated,
            "matchedRules": list(self.matched_rules),
        }


def redact_pii(text: str) -> tuple[str, int]:
    """把手机号/邮箱/身份证替换为掩码，返回 (治理后文本, 替换次数)。

    掩码保留头尾（手机号前 3 后 4、身份证前 6 后 4），既消除可识别性又保留
    可读性；邮箱保留本地部分前 2 与完整域名，方便模型区分"哪个邮箱被脱敏"。
    """

    count = 0

    def mask(match: re.Match[str], *, keep_head: int, keep_tail: int = 0) -> str:
        nonlocal count
        count += 1
        value = match.group(0)
        if len(value) <= keep_head + keep_tail:
            return value[:keep_head] + "*" * max(len(value) - keep_head, 1)
        return (
            value[:keep_head]
            + "*" * (len(value) - keep_head - keep_tail)
            + value[-keep_tail:]
        )

    def mask_email(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        value = match.group(0)
        local, sep, domain = value.partition("@")
        if not sep or not local or not domain:
            return value
        return local[:2] + "*" * max(len(local) - 2, 1) + "@" + domain

    text = _PHONE_RE.sub(lambda m: mask(m, keep_head=3, keep_tail=4), text)
    text = _EMAIL_RE.sub(mask_email, text)
    text = _IDCARD_RE.sub(lambda m: mask(m, keep_head=6, keep_tail=4), text)
    return text, count


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    # 说明：被省略的剩余内容不保留在任何地方（含 trace）——这是隐私设计，
    # 超出观测窗口的原文不出治理层；trace 只留下 originalLength 供估算。
    marker = "\n…（观察层已截断，超长原文未保留）"
    return text[: max(0, max_chars - len(marker))] + marker, True


def build_tool_observation(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    extra_omitted: list[str] | None = None,
    scan_injection: bool = True,
) -> ToolObservation:
    """治理一段工具结果文本，返回模型看到的版本与治理痕迹。

    ``scan_injection`` 默认开启：所有把内容放进模型上下文的地方都是通道边界，
    新调用点不显式传参也会自动获得污染扫描。扫描的是**治理后**的文本
    （脱敏/裁剪之后的版本）——模型实际看到什么，就据此判定污染。
    """

    original_len = len(text or "")
    redacted, redacted_count = redact_pii(text or "")
    bounded, truncated = truncate(redacted, max_chars=max_chars)
    omitted = list(extra_omitted or [])
    if truncated:
        omitted.append("content_overflow")
    observation = ToolObservation(
        text=bounded,
        original_len=original_len,
        truncated=truncated,
        redacted_count=redacted_count,
        omitted_fields=omitted,
    )
    if scan_injection and observation.text:
        verdict = scan_external_content(observation.text)
        observation.contaminated = verdict.contaminated
        observation.matched_rules = verdict.matched_rules
        if verdict.contaminated:
            observation.text = CONTAMINATED_CONTENT_PLACEHOLDER
            observation.omitted_fields.append("contaminated_content")
    return observation


def build_tool_result_observation(
    result: object,
    *,
    fallback: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolObservation:
    """治理完整工具结果，并扫描可能直达前端的结构化文本。

    工具 ``content`` 会进入模型上下文，而 ``assistant_cards``、``biz_data``、
    商品名和来源信息可能绕过模型直接进入最终响应。因此污染判定覆盖这些字段；
    一处命中即检疫整次结果，具体的结构化字段丢弃由调用方执行。
    """

    to_tool_message = getattr(result, "to_tool_message", None)
    raw_text = (
        to_tool_message()
        if callable(to_tool_message)
        else getattr(result, "content", "")
    )
    observation = build_tool_observation(
        str(raw_text or fallback),
        max_chars=max_chars,
    )

    related_payloads = (
        getattr(result, "assistant_cards", None),
        getattr(result, "biz_data", None),
        getattr(result, "product_names", None),
        getattr(result, "source_refs", None),
        getattr(result, "retrieval_trace", None),
    )
    matched = list(observation.matched_rules)
    contaminated = observation.contaminated
    for payload in related_payloads:
        if payload in (None, "", [], {}):
            continue
        if isinstance(payload, str):
            payload_text = payload
        else:
            payload_text = json.dumps(payload, ensure_ascii=False, default=str)
        verdict = scan_external_content(payload_text)
        if verdict.contaminated:
            contaminated = True
            matched.extend(verdict.matched_rules)

    if contaminated:
        observation.contaminated = True
        observation.matched_rules = tuple(dict.fromkeys(matched))
        observation.text = CONTAMINATED_CONTENT_PLACEHOLDER
        if "contaminated_content" not in observation.omitted_fields:
            observation.omitted_fields.append("contaminated_content")
    return observation
