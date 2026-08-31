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
from app.harness.guardrails.query_security import separate_explicit_attack_suffix

logger = structlog.get_logger()

# HTML 内容检测：标签密度阈值
# 计算 "<x" 或 "</x" 形式的标签数量相对于文本长度的比例
_HTML_TAG_PATTERN = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*[\s>/]")
# 开头即是 HTML 文档结构的标志
_HTML_DOC_PREFIX = re.compile(r"^\s*(<\s*!DOCTYPE\s+html|<\s*html\b)", re.I)
# 触发 HTML 友好提示的密度阈值（标签数/总字符数）
_HTML_TAG_DENSITY_THRESHOLD = 0.08

# Credential material must never be echoed by a public shopping assistant.
# Match an explicit exfiltration action together with a credential term in
# either order.  Definition/configuration questions ("Authorization 是什么")
# intentionally do not contain one of the strong output actions below.
_CREDENTIAL_EXFILTRATION = re.compile(
    r"(?:(?:(?:完整|全部|原文|真实|当前|所有)\s*)?"
    r"(?:打印|输出|显示|展示|导出|泄露|返回|贴出|复制|dump|print|show|reveal|output|export|leak|disclose)"
    r"[^。；\n]{0,40}(?:authorization|api\s*key|api密钥|密钥|private[_\s-]?key|"
    r"access\s*token|refresh\s*token)"
    r"|(?:authorization|api\s*key|api密钥|密钥|private[_\s-]?key|access\s*token|"
    r"refresh\s*token)[^。；\n]{0,40}"
    r"(?:(?:完整|全部|原文|真实|当前|所有)\s*)?"
    r"(?:打印|输出|显示|展示|导出|泄露|返回|贴出|复制|dump|print|show|reveal|output|export|leak|disclose))",
    re.I | re.X,
)

# A small set of high-confidence compound attacks that are otherwise easy to
# miss when their individual words are intentionally kept as weak signals.
# These patterns require both an exfiltration/override action and a sensitive
# target; ordinary HTML pasted for product support, policy documentation about
# prompt injection, and standalone words such as ``DAN`` remain untouched.
_HTML_EXFILTRATION = re.compile(
    r"<\s*(?:script|iframe|object|html)\b[^>]*>.*?"
    r"</\s*(?:script|iframe|object|html)\s*>"
    r"(?:请|帮|要求)?\s*(?:泄露|输出|打印|回显|显示|导出|dump|print|reveal|exfiltrat)"
    r"[^。；\n]{0,40}(?:secret|token|密钥|隐藏上下文|系统提示)",
    re.I | re.S,
)
_OVERRIDE_HIDDEN_CONTEXT_ZH = re.compile(
    r"(?:覆盖|替换|无视)[^。；\n]{0,12}"
    r"(?:开发者|系统|安全)[^。；\n]{0,8}(?:指令|规则|提示)"
    r"[^。；\n]{0,12}(?:回显|输出|打印|泄露|展示)[^。；\n]{0,16}"
    r"(?:隐藏上下文|内部上下文|系统提示|prompt)",
    re.I,
)
_JAILBREAK_BYPASS_ZH = re.compile(
    r"(?:启用|进入|开启|使用)?\s*(?:dan|DAN)\s*模式"
    r"[^。；\n]{0,24}(?:绕过|无视|突破)[^。；\n]{0,16}"
    r"(?:引用约束|安全规则|防护|限制|校验)",
    re.I,
)
_PROMPT_EXFILTRATION_REVERSE_ZH = re.compile(
    r"(?:检索到|获取到|读取到|找到|看到)[^。；\n]{0,12}"
    r"(?:系统提示词|系统提示|开发者指令|隐藏上下文|内部提示)"
    r"[^。；\n]{0,12}(?:原样|完整|全部)?\s*"
    r"(?:输出|打印|回显|复述|展示|泄露)",
    re.I,
)
_INTERNAL_ASSET_EXFILTRATION = re.compile(
    r"(?:^|[。！？；;\n]\s*)"
    r"(?=[^。；;\n]{0,96}(?:输出|打印|列出|枚举|展示|显示|导出|泄露|复制|"
    r"发送|返回|提供|贴出|告诉|dump|print|list|show|reveal|output|export|copy|"
    r"send|return|provide|tell))"
    r"(?:"
    r"(?=[^。；;\n]{0,96}(?:内部|未公开|隐藏|后台|internal|hidden|private|"
    r"non[- ]?public))"
    r"(?=[^。；;\n]{0,96}(?:工具|函数|接口|tool|function|api))"
    r"(?=[^。；;\n]{0,96}(?:定义|清单|列表|schema|参数|签名|协议|definition|"
    r"spec|signature))"
    r"|(?=[^。；;\n]{0,96}(?:未公开|隐藏|私有|管理员|开发者|hidden|private|"
    r"admin|developer|non[- ]?public))"
    r"(?=[^。；;\n]{0,96}(?:指令|命令|要求|规则|提示|instruction|command|"
    r"request|rule|prompt))"
    r"|(?=[^。；;\n]{0,96}(?:其他用户|全部用户|全量用户|私有用户|未公开用户|"
    r"other users?|all users?|private\s+(?:customer|user)|"
    r"protected\s+(?:customer|user)|admin\s+user))"
    r"(?=[^。；;\n]{0,96}(?:数据|记录|订单|地址|联系方式|日志|对话|记忆|data|"
    r"records?|orders?|addresses?|contacts?|logs?|conversations?|memory)))"
    r"[^。；;\n]{1,112}",
    re.I,
)
_CONFIRMATION_BYPASS_WRITE = re.compile(
    r"(?:^|[。！？；\n]\s*|(?:请|帮我|替我|给我|现在|立即|马上|务必|怎么|"
    r"please|now|immediately|how\s+to)\s*)"
    r"(?:"
    r"(?:(?:跳过|绕过|省略|免去|无需|不经|skip|bypass|without|no|do\s+not)"
    r"[^。；\n]{0,8}(?:用户|人工|user)?\s*(?:的)?(?:确认|confirm(?:ation)?)"
    r"|不要(?:再)?\s*(?:用户|人工)?(?:的)?确认)"
    r"[^。；\n]{0,24}(?:直接|立即|马上|directly|immediately)?\s*"
    r"(?:执行|提交|写入|退款|退货|取消订单|确认收货|发布评价|修改|删除|"
    r"使用优惠券|创建工单|关闭工单|下单|支付|execute|submit|write|refund|return|"
    r"cancel|modify|delete|redeem|create|close|purchase|pay)"
    r"|(?:直接|立即|马上|directly|immediately)\s*"
    r"(?:执行|提交|写入|退款|退货|取消订单|确认收货|发布评价|修改|删除|"
    r"使用优惠券|创建工单|关闭工单|下单|支付|execute|submit|write|refund|return|"
    r"cancel|modify|delete|redeem|create|close|purchase|pay)"
    r"[^。；\n]{0,24}"
    r"(?:(?:跳过|绕过|省略|免去|无需|不经|skip|bypass|without|no|do\s+not)"
    r"[^。；\n]{0,8}(?:用户|人工|user)?\s*(?:的)?(?:确认|confirm(?:ation)?)"
    r"|不要(?:再)?\s*(?:用户|人工)?(?:的)?确认)"
    r")",
    re.I,
)
_IMPERATIVE_TOOL_INVOCATION = re.compile(
    r"(?:^|[。！？；;\n]\s*)"
    r"(?=[^。；;\n]{0,72}(?:请|帮我|替我|给我|要求|命令|please|tell|instruct|ask))"
    r"(?=[^。；;\n]{0,72}(?:不要|勿|禁止|跳过|绕过|强制|指定|"
    r"do\s+not|must\s+not|never|skip|bypass|force|specified))"
    r"(?=[^。；;\n]{0,72}(?:调用|执行|运行|invoke|\bcall\b|execute|\brun\b))"
    r"(?=[^。；;\n]{0,72}(?:工具|函数|接口|tool|function|api|endpoint))"
    r"[^。；;\n]{1,96}",
    re.I,
)
_DIRECT_HUMAN_WRITE = re.compile(
    r"(?:^|[。！？；;\n]\s*)"
    r"(?=[^。；;\n]{0,80}(?:请|帮我|替我|给我|让|要求|指示|命令|叫|"
    r"please|tell|instruct|ask))"
    r"(?=[^。；;\n]{0,80}(?:指令|命令|要求|instruction|command))"
    r"(?=[^。；;\n]{0,80}(?:人工|客服|human|agent|support))"
    r"(?=[^。；;\n]{0,80}(?:确认|confirm(?:ation)?|approve|approval))"
    r"(?=[^。；;\n]{0,80}(?:直接|立即|马上|强制|directly|immediately|force))"
    r"(?=[^。；;\n]{0,80}(?:退款|退货|取消|确认收货|评价|修改|删除|优惠券|工单|"
    r"下单|支付|提交|写入|refund|return|cancel|review|modify|delete|coupon|ticket|"
    r"purchase|pay|submit|write))"
    r"[^。；;\n]{1,112}",
    re.I,
)

# 命中即拒。购物咨询场景里这些写法没有正常语义，误伤风险可以忽略。
_BLOCKING_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credential_exfiltration",
        _CREDENTIAL_EXFILTRATION,
    ),
    ("html_exfiltration", _HTML_EXFILTRATION),
    ("override_hidden_context_zh", _OVERRIDE_HIDDEN_CONTEXT_ZH),
    ("jailbreak_bypass_zh", _JAILBREAK_BYPASS_ZH),
    ("prompt_exfiltration_reverse_zh", _PROMPT_EXFILTRATION_REVERSE_ZH),
    ("internal_asset_exfiltration", _INTERNAL_ASSET_EXFILTRATION),
    ("confirmation_bypass_write", _CONFIRMATION_BYPASS_WRITE),
    ("imperative_tool_invocation", _IMPERATIVE_TOOL_INVOCATION),
    ("direct_human_write", _DIRECT_HUMAN_WRITE),
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

# 规范性否定句中的“不能绕过/不得跳过”是在描述系统边界，不是攻击指令。
# 规则本身仍保留对“请绕过风控”这类祈使句的拦截；在统一扫描函数中按命中
# 位置检查前缀，避免用一个脆弱的超长正则吞掉正常知识事实。
_NEGATED_BYPASS_PREFIX = re.compile(
    r"(?:不能够|不能|不可|无法|不应|不得|不要|请勿|禁止|严禁|不允许|不可以|"
    r"do\s+not|must\s+not|never)"
    r"[^。；\n]{0,12}$"
)
_BYPASS_REQUEST_PREFIX = re.compile(r"(?:请|帮|替|告诉|教|如何|怎么|方法|步骤)")
_NEGATION = re.compile(
    r"(?:不能够|不能|不可|无法|不应|不得|不要|请勿|禁止|严禁|不允许|不可以|"
    r"do\s+not|must\s+not|never)",
    re.I,
)
_SENSITIVE_ACTION = re.compile(
    r"输出|打印|展示|显示|导出|泄露|返回|提供|告诉|发送|发我|调用|执行|运行|"
    r"改用|指定|直接|立即|print|show|reveal|output|export|return|provide|tell|send|invoke|"
    r"\bcall\b|execute|\brun\b|directly|immediately",
    re.I,
)
_NEGATION_SCOPE_BREAK = re.compile(
    r"[，,；;]|但|但是|却|不过|然后|随后|接着|请|帮|把|将|怎么|如何|方法|"
    r"步骤|改用|指定|but|however|then|please|help|how\s+to",
    re.I,
)
_NORMATIVE_CONFIRMATION_POLICY = re.compile(
    r"(?:不能够|不能|不可|不应|不得|禁止|严禁|不允许|不可以|"
    r"cannot|must\s+not|never)"
    r"[^，,。；;\n]{0,8}(?:跳过|绕过|省略|无需|不经|skip|bypass|without)"
    r"[^，,。；;\n]{0,8}(?:用户|人工|user)?\s*(?:的)?(?:确认|confirm(?:ation)?)",
    re.I,
)
_COMPLIANT_HUMAN_CONFIRMATION = re.compile(
    r"按(?:平台|退款|售后|业务流程|服务流程|公开政策|平台政策|订单页面)要求"
    r"[^。；;\n]{0,40}(?:人工|客服|human|agent|support)"
    r"[^。；;\n]{0,20}(?:确认|confirm(?:ation)?|approve|approval)",
    re.I,
)


def _all_sensitive_actions_are_negated(text: str) -> bool:
    actions = list(_SENSITIVE_ACTION.finditer(text))
    negations = list(_NEGATION.finditer(text))
    if not actions or not negations:
        return False
    negation_index = 0
    latest_negation: re.Match[str] | None = None
    for action in actions:
        while (
            negation_index < len(negations)
            and negations[negation_index].end() <= action.start()
        ):
            latest_negation = negations[negation_index]
            negation_index += 1
        if latest_negation is None:
            return False
        between = text[latest_negation.end() : action.start()]
        if len(between) > 24 or _NEGATION_SCOPE_BREAK.search(between):
            return False
    return True


def scan_guardrail_rules(text: str) -> tuple[list[str], list[str]]:
    """Return blocking and suspicious rule IDs for normalized channel content.

    Input and external-channel guards must share this position-aware filtering.
    In particular, a knowledge sentence such as ``不能绕过归属校验`` is a
    legitimate negative policy fact, while an imperative ``请绕过风控`` is not.
    """

    blocking: list[str] = []
    for name, pattern in _BLOCKING_RULES:
        matches = list(pattern.finditer(text))
        if name == "guard_bypass_zh":
            retained = []
            for match in matches:
                prefix = text[max(0, match.start() - 20) : match.start()]
                negated = _NEGATED_BYPASS_PREFIX.search(prefix)
                request_like = _BYPASS_REQUEST_PREFIX.search(
                    prefix[negated.start() :] if negated else prefix
                )
                if not negated or request_like:
                    retained.append(match)
            matches = retained
        elif name == "confirmation_bypass_write":
            retained = []
            for match in matches:
                context = text[max(0, match.start() - 20) : match.end()]
                policy = _NORMATIVE_CONFIRMATION_POLICY.search(context)
                if not policy or not _all_sensitive_actions_are_negated(context):
                    retained.append(match)
            matches = retained
        elif name in {
            "internal_asset_exfiltration",
            "imperative_tool_invocation",
            "direct_human_write",
        }:
            retained = []
            for match in matches:
                context = text[
                    max(0, match.start() - 20) : min(len(text), match.end() + 80)
                ]
                compliant_human_flow = (
                    name == "direct_human_write"
                    and _COMPLIANT_HUMAN_CONFIRMATION.search(context)
                )
                if (
                    not compliant_human_flow
                    and not _all_sensitive_actions_are_negated(context)
                ):
                    retained.append(match)
            matches = retained
        if matches:
            blocking.append(name)
    suspicious = [name for name, pattern in _SUSPICIOUS_RULES if pattern.search(text)]
    return blocking, suspicious

# 公开别名：channel_guard（外部通道检疫）与这里共用同一张规则表，
# 保证"用户输入"与"知识库/工具内容"两处对注入话术的判定完全一致，
# 差异只在响应策略（拒绝 vs 检疫）。
BLOCKING_RULES = _BLOCKING_RULES
SUSPICIOUS_RULES = _SUSPICIOUS_RULES
SUSPICIOUS_BLOCK_THRESHOLD = _SUSPICIOUS_BLOCK_THRESHOLD

@dataclass(frozen=True)
class InputVerdict:
    """一次输入检查的结果。

    ``text`` 始终是可以继续用的净化文本；``blocked`` 为真时调用方应当拒绝这一轮。
    ``html_content`` 为真时输入疑似 HTML/网页源码，调用方可给出更友好的引导文案。
    ``matched_rules`` 只用于日志和后续统计，不参与业务判断。
    """

    text: str
    blocked: bool
    matched_rules: tuple[str, ...]
    html_content: bool = False


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

    @staticmethod
    def _is_html_content(text: str) -> bool:
        """判断输入是否疑似 HTML/网页源码。

        两种情况触发：
        1. 文本以 <!DOCTYPE html> 或 <html 开头（典型的完整页面粘贴）；
        2. HTML 标签密度超过阈值（分段粘贴的 HTML 片段）。

        不把这类输入直接 block——用户可能是想让客服帮忙看某个页面——而是
        通过 ``InputVerdict.html_content`` 标志让上层给出更友好的引导文案。
        """
        if _HTML_DOC_PREFIX.match(text):
            return True
        if len(text) < 50:
            return False
        tag_count = len(_HTML_TAG_PATTERN.findall(text))
        return tag_count / len(text) >= _HTML_TAG_DENSITY_THRESHOLD

    def _scan(self, text: str) -> tuple[list[str], list[str]]:
        return scan_guardrail_rules(text)

    def inspect(self, text: str | None) -> InputVerdict:
        """归一化并检查一段用户输入。

        归一化后的长度超限仍然抛 ``ValueError``（这是用户可修正的输入错误），
        注入判定则通过返回值表达，让调用方决定怎么响应。

        新增检查：
        - 有效字符数下限（``min_input_chars``）：低于阈值的极短消息（单字、单标点）
          触发友好引导，不进 LLM pipeline，避免单字轰炸消耗 token。
        - HTML 内容标记（``html_content``）：疑似网页源码时上层可返回引导文案，
          而非让 LLM 尝试解析无意义的 HTML，浪费 token 且容易产生幻觉。
        """
        normalized = self.normalize(text)
        separated = separate_explicit_attack_suffix(normalized)
        if separated.separated:
            normalized = separated.safe_query

        settings = get_settings()
        # 最小有效长度：去空白后字符数不足时直接返回引导提示标志。
        # 注意：这里不抛异常——单字/单标点是用户失误而非攻击，不应记安全日志。
        # 调用方检查 verdict.blocked + verdict.text 为空时给出引导文案。
        min_chars = settings.min_input_chars
        effective = normalized.strip()
        if min_chars > 0 and len(effective) < min_chars:
            return InputVerdict(
                text=effective,
                blocked=True,
                matched_rules=("too_short",),
                html_content=False,
            )

        html_content = self._is_html_content(normalized)

        blocking, suspicious = self._scan(normalized)

        cleaned = _ACT_TOKEN_PATTERN.sub("", normalized)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()

        blocked = bool(blocking) or len(suspicious) >= _SUSPICIOUS_BLOCK_THRESHOLD
        matched = tuple([*separated.security_flags, *blocking, *suspicious])
        if blocked:
            # 不记原文：输入可能含用户隐私，命中的规则名足够定位问题。
            logger.warning(
                "input_guard_blocked",
                rules=matched,
                blocking=tuple(blocking),
                text_length=len(normalized),
                html_content=html_content,
            )
        elif matched:
            logger.info("input_guard_suspicious", rules=matched, text_length=len(normalized))
        elif html_content:
            logger.info("input_guard_html_content", text_length=len(normalized))

        return InputVerdict(
            text=cleaned,
            blocked=blocked,
            matched_rules=matched,
            html_content=html_content,
        )

    def check_chat_limit(self, user_round_count: int) -> bool:

        settings = get_settings()
        if settings.ai_chat_limit <= 0:
            return True
        return user_round_count < settings.ai_chat_limit
