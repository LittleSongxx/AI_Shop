"""告警规则与实际 emit 的指标之间的一致性检查。

为什么需要这个文件：写错 label 值的告警不会报错，只会永远不触发。
`agent_rag_search_total{result="empty"}` 语法完全合法，但 retriever.py 只 emit
hit / miss，所以这条规则永远沉默——而 Grafana 里它显示为"正常"，比没有这条规则更糟，
因为它制造了"这块有监控"的错觉。同类问题在 result="failed"（实际是 dead）
上也发生过一次。

这类错误只能靠交叉比对代码来发现，人工 review 抓不住，所以做成测试。
覆盖范围只限 agent_* 指标——http_server_requests_* / jvm_* / up 来自 Micrometer
和 Prometheus 自身，不在本仓库定义。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_AGENT_ROOT = Path(__file__).resolve().parents[1]
_ALERTS = _AGENT_ROOT.parents[1] / "deploy/grafana/provisioning/alerting/aishop-alerts.yml"
_SENSORS = _AGENT_ROOT / "app/harness/metrics/runtime_sensors.py"


def _load_rules() -> list[dict]:
    data = yaml.safe_load(_ALERTS.read_text(encoding="utf-8"))
    return [rule for group in data["groups"] for rule in group["rules"]]


def _all_exprs() -> list[str]:
    exprs = []
    for rule in _load_rules():
        for node in rule["data"]:
            expr = node.get("model", {}).get("expr")
            if expr:
                exprs.append(expr)
    return exprs


def _declared_metric_names() -> set[str]:
    """从 runtime_sensors.py 里取指标名，而不是在测试里手抄一份。"""
    source = _SENSORS.read_text(encoding="utf-8")
    return set(re.findall(r'(?:Counter|Gauge|Histogram)\(\s*\n?\s*"([a-z_]+)"', source))


def _labels_call_spans(source: str, metric_const: str) -> list[str]:
    """取出 `CONST.labels(...)` 括号内的原文，按括号配对而不是按正则。

    单纯用 `[^)]*` 取不全，因为实参里本身有括号
    （`queue=str(payload.get("queueName") or "unknown")`），
    正则会在第一个 `)` 就停下，于是后面的 `result="dead"` 永远扫不到。
    """
    spans: list[str] = []
    needle = f"{metric_const}.labels("
    start = source.find(needle)
    while start != -1:
        index = start + len(needle)
        depth = 1
        while index < len(source) and depth:
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
            index += 1
        spans.append(source[start + len(needle) : index - 1])
        start = source.find(needle, index)
    return spans


def _emitted_label_values(metric_const: str, label: str) -> set[str]:
    """扫全仓库 `.labels(label=...)` 的调用，取出真实 emit 过的取值。

    取值可能不是字面量，而是条件表达式
    （`result="hit" if hit else "miss"`），所以从 `label=` 之后到下一个顶层逗号
    之间的所有字符串字面量都算。
    """
    values: set[str] = set()
    assign = re.compile(rf"\b{label}\s*=\s*")
    for path in (_AGENT_ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if metric_const not in source:
            continue
        for span in _labels_call_spans(source, metric_const):
            match = assign.search(span)
            if not match:
                continue
            tail, depth = [], 0
            for char in span[match.end() :]:
                if char in "([{":
                    depth += 1
                elif char in ")]}":
                    depth -= 1
                elif char == "," and depth == 0:
                    break
                tail.append(char)
            values.update(re.findall(r'"([^"]+)"', "".join(tail)))
    return values


def test_alert_file_is_valid_provisioning_yaml():
    data = yaml.safe_load(_ALERTS.read_text(encoding="utf-8"))
    assert data["apiVersion"] == 1
    assert data["groups"], "没有任何告警组"


def test_every_rule_points_at_the_provisioned_datasource():
    """uid 对不上 datasources/prometheus.yml，规则导入后不会求值。"""
    for rule in _load_rules():
        uids = {node["datasourceUid"] for node in rule["data"]}
        assert uids <= {"aishop-prometheus", "__expr__"}, rule["uid"]


def test_rule_uids_are_unique():
    uids = [rule["uid"] for rule in _load_rules()]
    assert len(uids) == len(set(uids))


def test_every_rule_has_severity_and_a_runbook_style_description():
    """只说"某个值高了"的告警没法处置，必须带下一步往哪看。"""
    for rule in _load_rules():
        assert rule["labels"]["severity"] in {"critical", "warning"}, rule["uid"]
        assert rule["annotations"]["summary"].strip(), rule["uid"]
        assert len(rule["annotations"]["description"].strip()) > 40, rule["uid"]


def test_every_rule_waits_out_a_scrape_blip():
    """scrape_interval 是 15s，for 太短会被单次抓取抖动触发。"""
    for rule in _load_rules():
        assert rule["for"] not in (None, "", "0s"), f"{rule['uid']} 没有 for，会立刻触发"


def test_agent_metric_names_in_alerts_actually_exist():
    declared = _declared_metric_names()
    assert declared, "没能从 runtime_sensors.py 解析出指标名，正则要跟着改"

    for expr in _all_exprs():
        for referenced in set(re.findall(r"\b(agent_[a-z_]+)\b", expr)):
            # Histogram 会派生 _bucket / _sum / _count 三个序列。
            base = re.sub(r"_(bucket|sum|count)$", "", referenced)
            assert base in declared, f"告警引用了不存在的指标 {referenced}"


def test_agent_metrics_are_scoped_to_their_prometheus_jobs():
    """避免 API/Worker 的同名默认值或其他抓取目标污染聚合结果。"""
    matcher = re.compile(r"\b(agent_[a-z_]+)(?:\{([^}]*)\})?")
    for expr in _all_exprs():
        for metric, labels in matcher.findall(expr):
            assert re.search(r'\bjob\s*(?:=|=~)\s*"aishop-agent', labels), (
                f"{metric} 缺少 aishop-agent job 过滤：{expr}"
            )


def test_task_metrics_use_their_authoritative_process():
    expressions = "\n".join(_all_exprs())
    assert 'agent_task_backlog{job="aishop-agent"}' in expressions
    assert 'agent_task_total{job="aishop-agent-worker"' in expressions


@pytest.mark.parametrize(
    ("metric", "const", "label"),
    [
        ("agent_rag_search_total", "RAG_SEARCH_TOTAL", "result"),
        ("agent_task_total", "AGENT_TASK_TOTAL", "result"),
        ("agent_tool_call_total", "TOOL_CALL_TOTAL", "status"),
    ],
)
def test_alert_label_matchers_use_values_the_code_emits(metric, const, label):
    """这条就是那两次 bug 的防线：写了永不触发的 label 值要红。"""
    emitted = _emitted_label_values(const, label)
    assert emitted, f"没扫到 {const} 的 {label} 取值，正则要跟着改"

    matcher = re.compile(rf'{metric}\{{[^}}]*{label}\s*(=|=~|!=)\s*"([^"]+)"')
    checked = 0
    for expr in _all_exprs():
        for op, value in matcher.findall(expr):
            candidates = value.split("|") if op == "=~" else [value]
            for candidate in candidates:
                assert candidate in emitted, (
                    f"{metric}{{{label}{op}\"{candidate}\"}} 永远不会匹配到样本，"
                    f"实际 emit 的是 {sorted(emitted)}"
                )
                checked += 1
    assert checked, f"{metric} 在告警里没有任何 {label} 过滤，这个测试没测到东西"


def test_ratio_alerts_guard_against_divide_by_zero():
    """没有流量时不做保护会得到 NaN，NaN 参与比较会产生假告警。"""
    for expr in _all_exprs():
        if "/" in expr:
            assert "clamp_min" in expr, f"比率表达式缺少 clamp_min 兜底：{expr}"
