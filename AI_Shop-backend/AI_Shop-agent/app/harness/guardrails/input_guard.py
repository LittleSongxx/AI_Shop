"""输入侧防护：归一化 + 提示注入启发式识别。

这一层只是纵深防御，不是边界。真正拦住提示注入的是另外三件事：

1. ``app/utils/prompt_boundary.py`` 把用户输入转义后包进 ``<user_input>``，
   并在系统词里声明标签内是数据而非指令（spotlighting）；知识库片段同样隔离。
2. 写操作只能由 ``PROPOSE_*`` 产出待确认单，token 由服务端签发、按归属校验，
   模型输出什么文本都改不了数据。
3. 工具白名单在 ``tool_guard`` 里，不由对话内容决定。

所以下面的规则表不追求"拦住所有注入"——那做不到。字符级混淆、多语种改写、
语义等价句都能绕过关键词匹配，把关键词表当边界只会给出虚假的安全感。它的作用是
以极低成本拦掉明文攻击，并为其余情况留下可观测信号。

规则分两档：``_BLOCKING_RULES`` 命中即拒（这些写法在购物咨询里没有正常用法），
``_SUSPICIOUS_RULES`` 是弱信号，单独命中只记录不拦截，命中两类以上才升级为拒绝。
这样划分是为了压住误伤：正常用户会问"提示词"这种词，但不会同时又要求忽略规则。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()

# 命中即拒。购物咨询场景里这些写法没有正常语义，误伤风险可以忽略。
_BLOCKING_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 只保留"指令类"宾语。原来把 message/context 也算进来会误伤正常的重置说法
    # （"ignore my previous message, show me white ones"），那是改需求不是攻击。
    (
        "instruction_override",
        re.compile(
            r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?"
            r"(previous|prior|above|earlier|preceding)\s+"
            r"(instruction|prompt|rule|direction)",
            re.I,
        ),
    ),
    # 同理，宾语不能收 限制/要求/条件：用户改搜索条件时会说"忽略之前的价格限制"、
    # "忘掉之前的要求"，那是正常导购对话，不该拒。
    (
        "instruction_override_zh",
        re.compile(
            r"(忽略|忽视|无视|忘记|忘掉|抛开)[^。；\n]{0,4}"
            r"(前面|上面|之前|以上|先前|原有|所有|全部|你的|系统)[^。；\n]{0,8}"
            r"(指令|命令|规则|设定|约束|系统提示词?|提示词)"
        ),
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"(show|print|reveal|repeat|output|display|dump|leak|tell)\s+(me\s+)?"
            r"(your|the)\s+(system|initial|original|full|hidden|internal|exact)\s*"
            r"(prompt|instruction|message|rule|config)",
            re.I,
        ),
    ),
    (
        "prompt_exfiltration_zh",
        re.compile(
            r"(输出|打印|复述|重复|展示|显示|告诉我|泄露|导出|背诵|贴出)[^。；\n]{0,8}"
            r"(系统提示词|系统提示|系统指令|初始指令|完整指令|原始指令|你的设定|"
            r"你的规则|工具定义|prompt)"
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(act\s+as|pretend\s+(you\s+are|to\s+be)|you\s+are\s+now)\s+"
            r"(an?\s+)?(unrestricted|uncensored|unfiltered|jailbroken|evil|"
            r"different\s+ai|dan\b)"
            r"|\b(dan|do\s+anything\s+now)\s+mode\b"
            r"|developer\s+mode\s+(on|enabled|activated)",
            re.I,
        ),
    ),
    # "没有任何限制"必须跟在角色指派后面才算攻击。单独出现是正常问句：
    # "这张券没有任何限制吗？"
    (
        "role_hijack_zh",
        re.compile(
            r"(开发者模式|越狱模式|上帝模式|无视一切规则|无视所有规则)"
            r"|(你现在是|你就是|扮演|假装你是|进入)[^。；\n]{0,8}"
            r"(不受任何限制|没有任何限制|没有限制|无限制|越狱|不受约束)"
        ),
    ),
    (
        "template_injection",
        re.compile(
            r"<\|\s*(im_start|im_end|system|endoftext|start_header_id|end_header_id|eot_id)\s*\|>"
            r"|\[/?INST\]|<</?SYS>>"
            r"|<\s*/?\s*(system|developer|tool_call|function_call|user_input)\b",
            re.I,
        ),
    ),
    # 只匹配"绕过防护"这一类宾语。"解除账号限制"、"关闭安全提醒"是客服场景的正常诉求，
    # 所以不收"限制""检查"这种在电商语境里高频的中性词。
    (
        "guard_bypass",
        re.compile(
            r"(bypass|disable|turn\s+off|circumvent|override)\s+"
            r"(the\s+|your\s+|all\s+)?(guardrail|safeguard|content\s+polic|moderation|"
            r"safety\s+(filter|check|guideline)|(your|the)\s+(safety|security|filter))",
            re.I,
        ),
    ),
    (
        "guard_bypass_zh",
        re.compile(
            r"(绕过|跳过|关闭|停用|解除|禁用|突破)[^。；\n]{0,6}"
            r"(校验|审核|风控|过滤器|敏感词|安全策略|安全机制|内容审查|防护机制|你的权限)"
        ),
    ),
)

# 弱信号。单独出现可能只是用户好奇或正常提问，需要两类以上同时命中才拦。
_SUSPICIOUS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mentions_system_prompt", re.compile(r"system\s*prompt|系统提示词|提示词", re.I)),
    (
        "mentions_meta_role",
        re.compile(r"(developer|system|assistant)\s+message|开发者消息|系统消息", re.I),
    ),
    # 不收"忘记"：忘记密码是客服高频问法，留着只会刷日志。
    ("mentions_ignore", re.compile(r"\b(ignore|disregard|override)\b|忽略|无视", re.I)),
    ("mentions_jailbreak", re.compile(r"jailbreak|越狱|prompt\s*injection|提示注入", re.I)),
    (
        "mentions_tool_internals",
        re.compile(
            r"(工具|tool)\s*(定义|列表|schema|definition)|function\s+calling|tool_choice",
            re.I,
        ),
    ),
    (
        "direct_tool_call",
        re.compile(r"(直接|强制|立即|马上)[^。；\n]{0,4}(调用|执行|运行)[^。；\n]{0,8}(工具|接口|函数|命令)"),
    ),
    (
        "encoded_payload",
        re.compile(r"(base64|rot13|hex|url)\s*(decode|decoded|解码|解密)", re.I),
    ),
    # 伪造对话轮次头。放在弱信号档是因为商品参数粘贴会误伤——"System: Android 14"
    # 在导购场景里是正常输入，单独命中不该直接拒。
    (
        "fake_turn_header",
        re.compile(r"^\s*(system|assistant|developer|human|user)\s*[:：]\s*\S", re.I | re.M),
    ),
)

# 用户不可能合法地在聊天正文里携带 act token：确认/取消走独立的 actionToken 表单字段。
# 正文里出现只有两种来源——复制粘贴助手上一轮的输出，或试图伪造。一律剥掉。
_ACT_TOKEN_PATTERN = re.compile(r"【?\s*act_[a-f0-9]{8,}\s*】?", re.I)

_SUSPICIOUS_BLOCK_THRESHOLD = 2

@dataclass(frozen=True)
class InputVerdict:
    """一次输入检查的结果。

    ``text`` 始终是可以继续用的净化文本；``blocked`` 为真时调用方应当拒绝这一轮。
    ``matched_rules`` 只用于日志和后续统计，不参与业务判断。
    """

    text: str
    blocked: bool
    matched_rules: tuple[str, ...]


class InputGuardrail:

    def __init__(self, sensitive_words: list[str] | None = None):

        self._sensitive_words = sensitive_words or []

    def filter_sensitive(self, text: str) -> str:

        result = text
        for word in self._sensitive_words:
            if word:
                result = result.replace(word, "*" * len(word))
        return result

    def normalize(self, text: str | None) -> str:
        """NFKC 折叠 + 去控制字符 + 压空白 + 长度上限。

        顺序有意义：NFKC 先把全角、兼容字符折成等价 ASCII，再删 Unicode ``C*``
        类别（零宽空格、双向覆写等），否则用 ``ｉｇｎｏｒｅ`` 或 ``ig​nore``
        这类写法就能躲开后面所有正则。
        """
        value = unicodedata.normalize("NFKC", text or "")
        value = "".join(
            ch for ch in value
            if ch in "\n\r\t" or not unicodedata.category(ch).startswith("C")
        )
        value = re.sub(r"[ \t]+", " ", value).strip()
        max_len = get_settings().max_input_chars
        if len(value) > max_len:
            raise ValueError(f"输入内容不能超过{max_len}个字符")
        return value

    def _scan(self, text: str) -> tuple[list[str], list[str]]:
        blocking = [name for name, pattern in _BLOCKING_RULES if pattern.search(text)]
        suspicious = [name for name, pattern in _SUSPICIOUS_RULES if pattern.search(text)]
        return blocking, suspicious

    def inspect(self, text: str | None) -> InputVerdict:
        """归一化并检查一段用户输入。

        归一化后的长度超限仍然抛 ``ValueError``（这是用户可修正的输入错误），
        注入判定则通过返回值表达，让调用方决定怎么响应。
        """
        normalized = self.normalize(text)
        blocking, suspicious = self._scan(normalized)

        cleaned = _ACT_TOKEN_PATTERN.sub("", normalized)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()

        blocked = bool(blocking) or len(suspicious) >= _SUSPICIOUS_BLOCK_THRESHOLD
        matched = tuple(blocking + suspicious)
        if blocked:
            # 不记原文：输入可能含用户隐私，命中的规则名足够定位问题。
            logger.warning(
                "input_guard_blocked",
                rules=matched,
                blocking=tuple(blocking),
                text_length=len(normalized),
            )
        elif matched:
            logger.info("input_guard_suspicious", rules=matched, text_length=len(normalized))

        return InputVerdict(text=cleaned, blocked=blocked, matched_rules=matched)

    def check_chat_limit(self, user_round_count: int) -> bool:

        settings = get_settings()
        if settings.ai_chat_limit <= 0:
            return True
        return user_round_count < settings.ai_chat_limit
