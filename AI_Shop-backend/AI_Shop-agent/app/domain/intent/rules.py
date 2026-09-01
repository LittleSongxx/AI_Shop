import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.category_terms import has_bare_bag_category

# 转人工的显式请求。放在 rules 层是因为 classifier 和 product_consult 都要先于
# 商品咨询分支拦截它：用户在咨询一款商品时要求转人工，不能被 PRODUCT_CONSULT 吃掉。
HUMAN_HINTS = (
    "转人工",
    "转交人工",
    "转个人",
    "人工客服",
    "找客服",
    "找真人",
    "真人客服",
    "真人处理",
    "人工处理",
    "人工介入",
    "人工确认",
    "人工核对",
    "人工核账",
    "人工更正",
    "安全人工",
    "安全专员",
    "找你们主管",
)

_PHONE_HINTS = ("手机", "iphone", "苹果", "三星", "华为", "小米", "oppo", "vivo", "荣耀")
_SNACK_HINTS = (
    "零食", "小吃", "坚果", "糖果", "饼干", "雪饼", "旺旺", "汽水",
    "可乐", "雪碧", "芬达", "薯片", "巧克力",
)
_COMPUTER_HINTS = ("电脑", "台式", "台式机", "笔记本", "平板", "主机", "显示器")
_TOY_HINTS = ("玩具", "玩偶", "模型", "积木", "乐高")
_MUSIC_HINTS = ("吉他", "乐器", "钢琴", "尤克里里", "电子琴")
_OTHER_PRODUCT_HINTS = ("家电", "服饰", "衣服", "鞋子", "美妆", "护肤", "儿童")
# 高频数码小家电/外设品类词。枚举品类这条路有上限（冻结会话评测限制与变更记录第 2 节），
# 这里只是把最高频的几种补进去，不追求穷尽——LLM 兜底仍然存在。
_DIGITAL_LIFESTYLE_HINTS = (
    "耳机", "音箱", "键盘", "鼠标", "手表", "充电宝",
    "空气炸锅", "电饭煲", "扫地机器人", "空气净化器", "净水器", "净化器",
    "空调", "冰箱", "洗衣机", "风扇",
)
_ALL_PRODUCT_HINTS = (
    _PHONE_HINTS + _SNACK_HINTS + _COMPUTER_HINTS + _TOY_HINTS + _MUSIC_HINTS
    + _OTHER_PRODUCT_HINTS + _DIGITAL_LIFESTYLE_HINTS
)
_SWITCH_HINTS = ("转", "切换", "换品类", "换个", "不要这款", "不要这个", "看看别的", "别的品类", "跨品类")
_SEARCH_VERBS = (
    "搜索", "找", "推荐", "买", "购买", "采购", "想要", "需要", "看看", "有没有",
)
_COMPARISON_HINTS = ("哪个好", "哪款好", "相比", "对比", "比较", "怎么选", "如何选")
_CURRENT_PRODUCT_REFERENCES = (
    "这款", "这副", "这个", "这台", "该款", "此款", "另一款", "另一副",
    "另一个", "这两个", "这两款",
)
# Attribute/compatibility vocabulary is deliberately narrower than the generic
# product vocabulary: "推荐降噪耳机" must remain a discovery query.
_PRODUCT_ATTRIBUTE_QUERY_HINTS = (
    "支持", "是否", "能否", "可以吗", "有没有", "有无", "适配", "兼容",
    "续航", "版本", "参数", "配置", "内存", "硬盘", "接口", "尺寸",
    "主动降噪", "防水", "保修", "怎么用",
)
_CONSULT_FOLLOWUP_HINTS = (
    "这款",
    "这个",
    "规格",
    "接口",
    "尺寸",
    "内存",
    "硬盘",
    "价格",
    "库存",
    "颜色",
    # 无商品快照时（from_product=True 但卡丢失）仍要认的咨询问法：
    # 带明确的规格/疑问标记才路由进咨询分支，"谢谢""嗯"这类寒暄不算。
    # （"可以"已移除：它是纯应答词，无卡时一句"可以"不该被路由进咨询分支，
    # 与 docstring 的"寒暄不进咨询"声明矛盾——P1 审查）
    "支持",
    "适配",
    "兼容",
    "有没有",
    "有无",
    "几个",
    "多少",
    "哪个",
    "哪些",
    "能不能",
    "多大",
    "怎么用",
    "怎么样",
    "参数",
    "型号",
    "配置",
    "发货",
    "售后",
    "保修",
    "介绍",
    "单主机",
    "显示器",
    "版本",
)

def looks_like_hot_sale_recommend(user_text: str) -> bool:

    if not user_text:
        return False
    t = user_text.strip()
    return any(k in t for k in ("热销", "热卖", "爆款")) or (
        "推荐" in t and any(k in t for k in ("商品", "好物"))
    )

def looks_like_browse_recommend(user_text: str) -> bool:

    if not user_text:
        return False
    t = user_text.strip()
    return any(k in t for k in ("浏览", "看过", "足迹")) or (
        "根据" in t and "推荐" in t
    )

def _text_lower(text: str) -> str:
    return (text or "").strip().lower()

def _mentions_any(text: str, hints: tuple[str, ...]) -> bool:
    t = _text_lower(text)
    return any(h in t for h in hints)

def _consult_product_category(consult_name: str | None) -> str | None:

    name = _text_lower(consult_name or "")
    if not name:
        return None
    if any(h in name for h in _PHONE_HINTS) and "电脑" not in name and "笔记本" not in name:
        return "phone"
    if any(h in name for h in _SNACK_HINTS):
        return "snack"
    if any(h in name for h in _COMPUTER_HINTS):
        return "computer"
    return "other"

def looks_like_category_switch(user_text: str, consult_product_name: str | None = None) -> bool:

    t = (user_text or "").strip()
    if not t:
        return False
    if any(h in t for h in _SWITCH_HINTS):
        return True

    if "怎么选" in t or "哪个好" in t or "对比" in t:
        if _mentions_any(t, _PHONE_HINTS) or _mentions_any(t, _COMPUTER_HINTS):
            consult_cat = _consult_product_category(consult_product_name)
            if consult_cat == "computer" and _mentions_any(t, _PHONE_HINTS):
                return True
            if consult_cat == "phone" and _mentions_any(t, _COMPUTER_HINTS):
                return True
            if consult_cat and consult_cat not in ("phone", "computer"):
                return True

    if re.search(r"预算\s*\d+", t) and any(v in t for v in ("买", "购", "想要", "推荐")):
        return True

    consult_cat = _consult_product_category(consult_product_name)
    if consult_cat == "computer":
        if _mentions_any(t, _PHONE_HINTS) and not _mentions_any(t, _COMPUTER_HINTS):
            return True
        if _mentions_any(t, _SNACK_HINTS):
            return True
    if consult_cat == "phone" and _mentions_any(t, _SNACK_HINTS):
        return True
    if _mentions_any(t, _TOY_HINTS + _MUSIC_HINTS):
        consult_name_lower = _text_lower(consult_product_name or "")
        if not any(h in consult_name_lower for h in _TOY_HINTS + _MUSIC_HINTS):
            return True

    if len(t) <= 12:
        if _mentions_any(t, _PHONE_HINTS) and consult_cat == "computer":
            return True
        if _mentions_any(t, _SNACK_HINTS) and consult_cat != "snack":
            return True
        if _mentions_any(t, _TOY_HINTS + _MUSIC_HINTS):
            return True
    return False

def looks_like_direct_product_keyword(user_text: str) -> bool:

    t = (user_text or "").strip()
    if not t or len(t) > 16:
        return False
    # Order-history phrases contain 「买」 but are not product search.
    # 与 _ORDER_LIST_UI_HINTS 对齐（order-007/009：漏了「上次买/再买一次/复购」
    # 会让订单历史问法被当成商品搜索）。
    if any(
        k in t
        for k in (
            "买了什么",
            "买过什么",
            "最近买",
            "最近购买",
            "我的订单",
            "最近的订单",
            "最近订单",
            "上次买",
            "再买一次",
            "复购",
        )
    ):
        return False
    if _mentions_any(t, _ALL_PRODUCT_HINTS):
        return True
    # 买点零食/买个手机 — classifier required; exclude 买了什么
    if re.search(r"买(点|些|个|一款)[\u4e00-\u9fff]{1,8}", t):
        return True
    return False

def looks_like_new_product_search(user_text: str) -> bool:

    t = (user_text or "").strip()
    if not t:
        return False
    from app.utils.order_ids import extract_order_id, extract_order_item_id

    if extract_order_item_id(t) or extract_order_id(t):
        return False
    if any(
        k in t
        for k in (
            "买了什么",
            "买过什么",
            "最近买",
            "最近购买",
            "我的订单",
            "最近的订单",
            "最近订单",
            "查订单",
            "上次买",
            "再买一次",
            "复购",
        )
    ):
        return False
    lower = t.casefold()
    if (
        "wps" in lower
        and "会员" in t
        and any(marker in lower for marker in ("有wps", "有没有", "想买", "要买", "购买", "找", "推荐"))
        and not any(marker in t for marker in ("已经买", "买了", "购买了", "没到账", "打不开", "怎么用", "什么功能"))
    ):
        return True
    # Do not widen a concrete attribute question into an arbitrary shelf
    # search. The current-product form covers "这款耳机支持蓝牙 5.4 吗";
    # the second form covers short colloquial wording such as
    # "耳机有主动降噪嘛" without matching "有没有耳机" discovery queries.
    attribute_query = any(marker in t for marker in _PRODUCT_ATTRIBUTE_QUERY_HINTS)
    if attribute_query:
        has_current_reference = any(marker in t for marker in _CURRENT_PRODUCT_REFERENCES)
        has_product_then_attribute = bool(
            re.search(
                r"(?:耳机|手机壳|手机|平板|电脑|笔记本|相机|音箱|键盘|鼠标|手表|家电)"
                r"\s*(?:支持|是否|能否|有没有|有无|适配|兼容|续航|版本|参数|配置|内存|硬盘|接口|尺寸|防水|保修|怎么用|有)"
                r"|(?:耳机|手机壳|手机|平板|电脑|笔记本|相机|音箱|键盘|鼠标|手表|家电)"
                r"[^。！？!?]{0,12}(?:吗|嘛|么|？|\?)$",
                t,
                flags=re.IGNORECASE,
            )
        )
        if has_current_reference or has_product_then_attribute:
            return False
    if looks_like_direct_product_keyword(t):
        return True
    if any(v in t for v in _SEARCH_VERBS) and (
        _mentions_any(t, _ALL_PRODUCT_HINTS)
        or has_bare_bag_category(t)
        or re.search(r"预算\s*(?:(?:提高|提升|调整|改成|改为|改|提到|放宽)\s*(?:到|至|为|成)?\s*)?\d+", t)
    ):
        return True

    # Category-selection questions are product search even without an explicit
    # "buy/recommend" verb. This keeps high-frequency requests such as
    # "新房除甲醛空气净化器怎么选" on the authoritative search path instead
    # of allowing an LLM intent fallback to classify them as generic chat.
    if any(k in t for k in ("怎么选", "如何选", "怎么挑", "如何挑", "哪个好")) and _mentions_any(
        t, _ALL_PRODUCT_HINTS
    ):
        return True

    if "推荐" in t and len(t) <= 40 and (
        _mentions_any(t, _PHONE_HINTS + _COMPUTER_HINTS + _TOY_HINTS + _MUSIC_HINTS)
        or "好物" in t
        or "商品" in t
        or "东西" in t
    ):
        return True
    return False


def looks_like_same_product_comparison(user_text: str | None) -> bool:
    """Keep a referenced-product comparison out of a fresh shelf search."""

    text = (user_text or "").strip()
    if not text or len(text) > 60:
        return False
    if not any(marker in text for marker in _COMPARISON_HINTS):
        return False
    if not any(marker in text for marker in _CURRENT_PRODUCT_REFERENCES):
        return False
    if not _mentions_any(text, _ALL_PRODUCT_HINTS):
        return False
    if any(verb in text for verb in ("搜索", "搜一下", "找一款", "推荐一款", "买一款")):
        return False
    if re.search(
        r"(?:和|与|跟)\s*(?:苹果|华为|小米|荣耀|三星|OPPO|vivo|索尼|联想|戴尔|惠普)",
        text,
        flags=re.IGNORECASE,
    ):
        return False

    # Cross-category comparisons remain on the broader search/comparison path;
    # a single category plus a referenced object is a consult clarification.
    category_hits = sum(
        1
        for family in (_PHONE_HINTS, _COMPUTER_HINTS, _SNACK_HINTS, _TOY_HINTS, _MUSIC_HINTS)
        if _mentions_any(text, family)
    )
    return category_hits <= 1 or any(
        marker in text for marker in ("另一款", "另一副", "这两个", "这两款")
    )

def looks_like_consult_followup(user_text: str) -> bool:

    t = (user_text or "").strip()
    if not t:
        return False
    if any(h in t for h in _CONSULT_FOLLOWUP_HINTS):
        return True
    if len(t) <= 20 and not any(v in t for v in _SEARCH_VERBS):
        return True
    return False


def looks_like_consult_question(user_text: str) -> bool:
    """无商品快照时判断是否仍像咨询问法（只认明确的咨询/规格标记）。

    与 looks_like_consult_followup 的区别：不含"短文本即算追问"的宽分支。
    没有商品上下文时，"谢谢""嗯"这类寒暄不能被路由进 PRODUCT_CONSULT，
    否则每句客套话都会被当成商品规格追问（P2 审查：from_product 误路由）。
    """
    t = _text_lower(user_text or "")
    return bool(t) and any(h in t for h in _CONSULT_FOLLOWUP_HINTS)

# 延续性问法的标记词：单独出现、不带任何新意图线索、很短时，
# 应该沿用上一轮意图而不是重新落到 CHAT（会话级意图保持，行业共识
# "意图不每轮重猜"——首轮完整识别，后续轮用轻量判断延续/切换）。
_CONTINUATION_MARKERS = ("那", "然后", "再", "还", "呢", "啊", "咋样", "怎么样了", "详情", "具体", "之后")

# 纯问候/应答：带语气词与标点变体也认（"你好啊""好的呢""还在吗"）。
# 这类句子绝不能触发意图延续——否则一句"你好啊"会复活 24 小时内的旧意图
# （P1 审查：A2 延续判定吞问候语）。
_GREETINGS = ("你好", "您好", "哈喽", "hello", "hi", "嗨", "在吗", "在不在", "在么")
_ACK_WORDS = frozenset({
    "好", "好的", "嗯", "嗯嗯", "嗯呢", "哦", "行", "行吧", "好嘞", "收到",
    "明白", "知道了", "是的", "对", "对的", "没问题", "可以", "谢谢", "感谢",
    "再见", "拜拜", "没事", "没事了", "没了", "没了呢", "好哒",
})

_DETERMINISTIC_SOCIAL_REPLIES = {
    "greeting": (
        frozenset({
            "你好", "你好啊", "您好", "您好啊", "哈喽", "哈喽啊", "hello", "hi", "嗨",
            "在吗", "在不在", "在么",
        }),
        "你好，我是 AI Shop 客服。请问需要查询订单、物流、优惠，还是推荐商品？",
    ),
    "acknowledgement": (
        frozenset({
            "好", "好的", "好的呢", "好嘞", "好哒", "收到", "明白", "知道了", "嗯", "嗯嗯",
            "嗯呢", "哦", "行", "行吧", "没问题", "可以", "对", "对的",
        }),
        "好的。",
    ),
    "thanks": (
        frozenset({"谢谢", "谢谢你", "感谢", "感谢你", "多谢", "辛苦了"}),
        "不客气，有需要可以继续告诉我。",
    ),
    "farewell": (
        frozenset({"再见", "拜拜", "回见"}),
        "再见，祝你购物愉快。",
    ),
}

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _shanghai_now() -> datetime:
    return datetime.now(_SHANGHAI_TZ)


def deterministic_social_reply(user_text: str | None) -> str | None:
    """Return a fixed reply only for a complete, bounded social utterance.

    This predicate is deliberately narrower than ``looks_like_ack_or_greeting``.
    The latter protects conversation-state heuristics and may accept a greeting
    embedded in a longer sentence; this function is a serving-path decision and
    therefore requires a full-string match.  Business-bearing text such as
    ``你好，帮我退款`` must continue through the normal classifier and Agent.
    """

    normalized = re.sub(
        r"^[\s~～!！?？。.,，、；;：:]+|[\s~～!！?？。.,，、；;：:]+$",
        "",
        str(user_text or ""),
    ).casefold()
    if not normalized:
        return None
    if re.fullmatch(
        r"(?:(?:你好|您好|哈喽|hello|hi|嗨)[，,\s]*)?"
        r"今天(?:是)?(?:周几|星期几)",
        normalized,
    ):
        now = _shanghai_now()
        return f"今天是{_WEEKDAY_NAMES[now.weekday()]}。"
    # A user may explicitly decline a human handoff while closing the
    # conversation.  This is still a bounded social turn, but only when the
    # complete utterance says that it is *just* a thank-you.  Keep the pattern
    # narrow so business-bearing text such as "不用转人工，我想问退款" cannot
    # bypass the normal intent and safety path.
    if re.fullmatch(
        r"(?:不用|不要|不需要)(?:转|找)?人工[，,\s]*"
        r"(?:我)?(?:只是)?(?:来)?(?:道谢|感谢|谢谢)(?:的)?",
        normalized,
    ):
        return _DETERMINISTIC_SOCIAL_REPLIES["thanks"][1]
    for phrases, reply in _DETERMINISTIC_SOCIAL_REPLIES.values():
        if normalized in phrases:
            return reply
    return None

def looks_like_ack_or_greeting(user_text: str | None) -> bool:
    """是否只是问候/应答/在场确认（可带语气词和标点）。

    用于两类防护：意图延续判定前排除（防止"你好啊"续上旧意图），以及
    A3 死循环检测前排除（防止 1 句真诉求 + 2 句应答凑满 3 连）。
    """
    t = (user_text or "").strip()
    if not t:
        return False
    if any(g in t for g in _GREETINGS):
        return True
    # 剥掉尾部语气词/标点后对纯应答词集合（"好的呢" → "好的"）。
    stripped = _strip_trailing_particles(t)
    return stripped in _ACK_WORDS

def _strip_trailing_particles(text: str) -> str:
    return re.sub(r"[的了吗呢啊呀哦呗哈吧啦~～!！?？。，,\s、，]+$", "", text)

def looks_like_intent_continuation(user_text: str, session_intent: str | None) -> bool:
    """文本是"上一轮话题的延续"时返回 True。

    保守判定，宁可漏判也不要抢走新意图：
    - 文本必须很短（≤10 字）——长文本大概率带了新信息；
    - 必须含延续标记词；
    - 不含任何强新意图词（转人工/退款/取消/订单号/品类词等）；
    - 不是纯问候/应答（"你好啊""好的呢"绝不延续旧意图）。
    """
    if not session_intent:
        return False
    t = (user_text or "").strip()
    if not t or len(t) > 10:
        return False
    if looks_like_ack_or_greeting(t):
        return False
    if not any(m in t for m in _CONTINUATION_MARKERS):
        return False
    if any(k in t for k in HUMAN_HINTS + _SWITCH_HINTS):
        return False
    if any(k in t for k in ("退款", "退货", "取消", "评价", "收货", "物流", "快递",
                            "优惠券", "优惠卷", "发票", "地址", "投诉", "人工")):
        return False
    if _mentions_any(t, _ALL_PRODUCT_HINTS):
        return False
    return True


_ORDER_LIST_UI_HINTS = (
    "我的订单",
    "最近订单",
    "最近的订单",
    "查订单",
    "订单列表",
    "买了什么",
    "买过什么",
    "最近买了",
    "最近买的",
    "最近购买",
    "再买一次",
    "复购",
    "上次买",
)

def wants_order_list_cards(user_text: str | None) -> bool:
    """UI 约定：这些问法必须渲染订单卡片，不能回 markdown 表格。

    原先 graph/nodes.py 和 services/agent_runtime.py 各存了一份完全相同的关键词表，
    改一处漏一处就会出现"判定要卡片但渲染不出卡片"。合并到规则层只留一份。
    """
    t = (user_text or "").strip()
    if not t:
        return False
    return any(k in t for k in _ORDER_LIST_UI_HINTS)
