from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str
    reason_code: str
    answer: str
    http_status: int


_DENY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(忽略.*(?:规则|指令)|prompt\s*injection).*(?:aishop_|源表|全部字段)"),
        "PROMPT_INJECTION_BLOCKED",
    ),
    (
        re.compile(r"(?i)(?:;\s*(?:drop|delete|update|insert|alter|truncate)\b|\b1\s*=\s*1\b.*--)"),
        "SQL_INJECTION_BLOCKED",
    ),
    (re.compile(r"(?i)(手机号|姓名和地址|身份证|用户地址|个人信息)"), "PII_ACCESS_PROHIBITED"),
    (
        re.compile(r"(?i)(跨库|information_schema|mysql\.|aishop_user\.)"),
        "CROSS_SCHEMA_ACCESS_PROHIBITED",
    ),
    (
        re.compile(
            r"(?i)(直接读取|查询).*(?:源表|refund_request|order_info|user_info).*(?:user_id|全部字段|退款金额)?"
        ),
        "SOURCE_TABLE_ACCESS_PROHIBITED",
    ),
    (
        re.compile(
            r"(?i)(?:(?:更新|修改|删除|写入|插入).*(?:stock|库存|数据|表)|"
            r"(?:stock|库存|数据|表).*(?:更新|修改|删除|写入|插入))"
        ),
        "WRITE_OPERATION_PROHIBITED",
    ),
)

_ABSTAIN_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"同比|环比"),
        "UNSUPPORTED_ANALYTIC_OPERATION",
        "V0 不支持同比、环比或窗口函数。",
    ),
    (
        re.compile(r"(?i)cohort|队列转化"),
        "UNSUPPORTED_COHORT_SEMANTICS",
        "当前漏斗只有各事件发生日汇总，不能形成曝光 cohort。",
    ),
    (
        re.compile(r"证明.*(?:导致|因果)|(?:推荐系统|推荐).*(?:导致|因果)"),
        "CAUSAL_CLAIM_UNSUPPORTED",
        "治理视图只能提供描述性事件数据，不能识别因果。",
    ),
    (
        re.compile(r"审计确认收入|结算收入|正式财务"),
        "FINANCIAL_METRIC_UNVERIFIED",
        "净支付额是暂定运营口径，不是审计或结算收入。",
    ),
    (
        re.compile(r"历史库存|(?:过去|历史).*(?:SKU|库存).*(?:快照|数量)?"),
        "HISTORICAL_INVENTORY_UNAVAILABLE",
        "库存风险视图只保留当前快照。",
    ),
    (
        re.compile(r"(?i)confidence.*(?:概率|缺货)|缺货.*概率"),
        "PROBABILITY_UNAVAILABLE",
        "confidence 只是有效销售日数据覆盖度，不是缺货概率。",
    ),
    (
        re.compile(r"(?i)\bjoin\b|售罄率"),
        "JOIN_OUT_OF_V0_SCOPE",
        "V0 不支持跨视图 Join，也没有已确认售罄率口径。",
    ),
    (
        re.compile(r"预测.*(?:销售收入|全站收入|销售额)"),
        "FORECAST_METRIC_UNAVAILABLE",
        "现有目录没有销售收入预测指标。",
    ),
    (
        re.compile(r"移动平均|滑动平均|窗口函数"),
        "WINDOW_FUNCTION_OUT_OF_V0_SCOPE",
        "V0 不支持窗口函数或移动平均。",
    ),
    (
        re.compile(r"实际发货.*(?:发生时间|今天)|按发货发生时间"),
        "FULFILLMENT_EVENT_TIME_UNAVAILABLE",
        "当前履约视图按订单创建日聚合当前状态，没有发货事件时间。",
    ),
)


def evaluate_question_policy(question: str, *, tenant_id: str | None) -> PolicyDecision | None:
    normalized = str(question or "").strip()
    tenant_match = re.search(r"(?i)tenant[-_a-z0-9]+", normalized)
    if tenant_id and tenant_match and tenant_match.group(0).lower() != tenant_id.lower():
        return PolicyDecision(
            outcome="DENY",
            reason_code="TENANT_SCOPE_VIOLATION",
            answer="请求超出当前租户范围。",
            http_status=403,
        )
    for pattern, reason_code in _DENY_RULES:
        if pattern.search(normalized):
            return PolicyDecision(
                outcome="DENY",
                reason_code=reason_code,
                answer="请求违反受治理分析访问策略，已拒绝。",
                http_status=403,
            )
    for pattern, reason_code, answer in _ABSTAIN_RULES:
        if pattern.search(normalized):
            return PolicyDecision(
                outcome="ABSTAIN",
                reason_code=reason_code,
                answer=answer,
                http_status=200,
            )
    return None
