"""外部通道内容的污染检疫（A2 工作线）。

``input_guard`` 治理的是"用户输入"——对话方在信任边界之外，命中注入话术
可以拒答。知识库片段、工具结果这些"通道内容"不同：它们来自系统自己的
数据源（Java 管理端上传的文档、业务库里的商品/订单文本），一段被污染的
文档不该让整个检索挂掉——拒绝服务本身也是损失。所以这里的响应策略是
**标记 + 检疫**而不是拒绝：

- 命中的片段在进入模型上下文前被剔除（quarantine），模型看到的是洁净集；
- 污染痕迹写进检索 trace / 观测记录，同时触发指标与告警日志，
  让"知识库出现被注入文档"这件事可被发现、可定位到具体文档；
- 整段被检疫干净时按"无证据"处理，绝不把污染内容喂给模型。

规则表与 ``input_guard`` 共用同一份（BLOCKING_RULES / SUSPICIOUS_RULES /
升级阈值），两处对"什么是注入话术"的判定完全一致，差异只在响应策略。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from app.harness.guardrails.input_guard import (
    BLOCKING_RULES,
    SUSPICIOUS_BLOCK_THRESHOLD,
    SUSPICIOUS_RULES,
)


@dataclass(frozen=True)
class ChannelVerdict:
    """一次通道内容扫描的结果。

    ``contaminated`` 为真时调用方应当检疫该片段（剔除/标记），
    而不是当作正常内容放行。``matched_rules`` 只用于日志与观测，
    不参与业务判断。
    """

    contaminated: bool
    matched_rules: tuple[str, ...] = ()


def scan_external_content(text: str | None) -> ChannelVerdict:
    """扫描一段外部通道内容，返回污染判定。

    检测逻辑与 ``input_guard.inspect`` 相同（同一张规则表、同样的升级阈值），
    但有两处刻意不同：

    1. 不做净化也不抛长度异常——原文属于调用方，通道内容不能因为
       扫描而被改写，检疫与否由调用方决定；
    2. 只在**副本**上做 NFKC 折叠与删控制字符，防全角/零宽字符绕过
       （"忽視"、"忽​视" 折回明文后同样命中），原文保持原样。
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = "".join(
        ch for ch in normalized
        if ch in "\n\r\t" or not unicodedata.category(ch).startswith("C")
    )
    blocking = [name for name, pattern in BLOCKING_RULES if pattern.search(normalized)]
    suspicious = [name for name, pattern in SUSPICIOUS_RULES if pattern.search(normalized)]
    contaminated = bool(blocking) or len(suspicious) >= SUSPICIOUS_BLOCK_THRESHOLD
    return ChannelVerdict(
        contaminated=contaminated,
        matched_rules=tuple(blocking + suspicious),
    )
