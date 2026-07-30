"""P2-2 RAGAS-style LLM-as-Judge evaluation for the AI_Shop RAG pipeline.

Metrics (0.0–1.0, averaged across cases with ``ground_truth_answer``):
  faithfulness      — answer only uses information present in retrieved context.
  context_precision — retrieved chunks are relevant to the question.
  answer_relevancy  — generated answer actually addresses the question asked.

Cases in rag_golden.jsonl that lack ``ground_truth_answer`` are skipped for
LLM-judge scoring but still contribute to the retrieval metrics block.

Quality gate: Faithfulness >= --min-faithfulness (default 0.80).
Exit code 1 on gate failure unless --no-fail is passed.

Usage:
    python scripts/eval_ragas.py
    python scripts/eval_ragas.py --min-faithfulness 0.75 --no-fail
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_MAX_JUDGE_TOKENS = 150
_MAX_GEN_TOKENS   = 180

# ── System prompts ─────────────────────────────────────────────────────────────

_SYS_FAITHFULNESS = (
    "你是 RAG 评测专家。判断【生成答案】的每个断言是否都有【检索上下文】支撑。\n"
    "只返回 JSON 对象：{\"score\":<0.0-1.0>,\"reason\":\"<一句话>\"}\n"
    "score 含义：1.0=完全忠实，0.0=存在幻觉或捏造信息。"
)
_SYS_CTX_PRECISION = (
    "你是 RAG 评测专家。判断【检索上下文】与【用户问题】的相关程度。\n"
    "只返回 JSON 对象：{\"score\":<0.0-1.0>,\"reason\":\"<一句话>\"}\n"
    "score 含义：1.0=完全相关，0.0=完全无关。"
)
_SYS_ANS_RELEVANCY = (
    "你是 RAG 评测专家。判断【生成答案】是否切题地回答了【用户问题】（不评忠实性）。\n"
    "只返回 JSON 对象：{\"score\":<0.0-1.0>,\"reason\":\"<一句话>\"}\n"
    "score 含义：1.0=完全切题，0.0=完全未回答问题。"
)
_SYS_GENERATE = (
    "你是电商客服，根据【检索上下文】回答用户问题。"
    "只用上下文信息作答；不含足够信息时回复\"抱歉，暂无相关信息\"；100字以内。"
)

# ── Low-level helpers ─────────────────────────────────────────────────────────


def _load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


async def _llm(messages: list[dict], max_tokens: int, temperature: float = 0.0) -> str:
    """One-shot LLM call; used for both generation and judging."""
    from app.config.settings import get_settings
    from app.infra.http_client import get_client

    settings = get_settings()
    client = await get_client("ragas_judge", timeout=20)
    resp = await client.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return (
        ((resp.json().get("choices") or [{}])[0])
        .get("message", {})
        .get("content", "")
        or ""
    ).strip()


def _parse_score(text: str) -> tuple[float, str]:
    """Extract (score, reason) from LLM JSON response; gracefully handles fences."""
    try:
        clean = (
            text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        data = json.loads(clean)
        score = float(data.get("score", 0.0))
        return max(0.0, min(1.0, score)), str(data.get("reason", ""))
    except Exception:
        return 0.0, f"parse_error: {text[:80]}"


def _context_text(rag_result: dict) -> str:
    return str(rag_result.get("text") or "").strip()


# ── LLM calls ─────────────────────────────────────────────────────────────────


async def _generate_answer(question: str, context: str) -> str:
    return await _llm(
        [
            {"role": "system", "content": _SYS_GENERATE},
            {
                "role": "user",
                "content": f"【检索上下文】\n{context}\n\n【用户问题】\n{question}",
            },
        ],
        max_tokens=_MAX_GEN_TOKENS,
        temperature=0.0,
    )


async def _judge_faithfulness(context: str, answer: str) -> tuple[float, str]:
    text = await _llm(
        [
            {"role": "system", "content": _SYS_FAITHFULNESS},
            {
                "role": "user",
                "content": (
                    f"【检索上下文】\n{context[:2000]}\n\n【生成答案】\n{answer}"
                ),
            },
        ],
        max_tokens=_MAX_JUDGE_TOKENS,
    )
    return _parse_score(text)


async def _judge_context_precision(question: str, context: str) -> tuple[float, str]:
    text = await _llm(
        [
            {"role": "system", "content": _SYS_CTX_PRECISION},
            {
                "role": "user",
                "content": (
                    f"【用户问题】\n{question}\n\n【检索上下文】\n{context[:2000]}"
                ),
            },
        ],
        max_tokens=_MAX_JUDGE_TOKENS,
    )
    return _parse_score(text)


async def _judge_answer_relevancy(question: str, answer: str) -> tuple[float, str]:
    text = await _llm(
        [
            {"role": "system", "content": _SYS_ANS_RELEVANCY},
            {
                "role": "user",
                "content": f"【用户问题】\n{question}\n\n【生成答案】\n{answer}",
            },
        ],
        max_tokens=_MAX_JUDGE_TOKENS,
    )
    return _parse_score(text)


# ── Per-case runner ───────────────────────────────────────────────────────────


async def _run_case(case: dict, rag_result: dict) -> dict[str, Any] | None:
    """Generate an answer and judge all three metrics for one golden case.

    Returns None for cases that have no ``ground_truth_answer`` (retrieval-only).
    """
    question = str(case.get("query") or "")
    if not str(case.get("ground_truth_answer") or "").strip():
        return None

    context = _context_text(rag_result)
    if not context:
        return {
            "query": question,
            "answer": "抱歉，暂无相关信息",
            "faithfulness": 1.0,       # nothing hallucinated (nothing said)
            "context_precision": 0.0,  # no context retrieved
            "answer_relevancy": 0.0,   # can't answer without context
            "notes": "no_context",
        }

    answer = await _generate_answer(question, context)
    (faith, faith_r), (ctx, ctx_r), (rel, rel_r) = await asyncio.gather(
        _judge_faithfulness(context, answer),
        _judge_context_precision(question, context),
        _judge_answer_relevancy(question, answer),
    )
    return {
        "query": question,
        "answer": answer,
        "faithfulness": faith,
        "faithfulness_reason": faith_r,
        "context_precision": ctx,
        "context_precision_reason": ctx_r,
        "answer_relevancy": rel,
        "answer_relevancy_reason": rel_r,
    }


# ── Main evaluation loop ──────────────────────────────────────────────────────


async def run(dataset: Path, top_k: int) -> dict[str, Any]:
    from app.rag.evaluation import evaluate_results
    from app.rag.retriever import rag_retriever

    cases = _load_cases(dataset)
    print(f"Loaded {len(cases)} cases from {dataset}", flush=True)

    rag_results = [
        await rag_retriever.search_faq_with_trace(case.get("query") or "", top_k=top_k)
        for case in cases
    ]

    judge_tasks = [_run_case(case, rag_results[i]) for i, case in enumerate(cases)]
    raw = await asyncio.gather(*judge_tasks, return_exceptions=True)

    judge_results: list[dict] = []
    for i, res in enumerate(raw):
        if isinstance(res, Exception):
            print(f"  [WARN] case {i} judge failed: {res}", flush=True)
        elif res is not None:
            judge_results.append(res)

    judge_metrics: dict[str, Any] = {"judgedCases": len(judge_results)}
    if judge_results:
        for metric in ("faithfulness", "context_precision", "answer_relevancy"):
            scores = [r[metric] for r in judge_results if metric in r]
            judge_metrics[metric] = round(sum(scores) / len(scores), 4) if scores else 0.0
        judge_metrics["perCase"] = judge_results
    else:
        judge_metrics.update(faithfulness=0.0, context_precision=0.0, answer_relevancy=0.0)

    retrieval_metrics = evaluate_results(cases, rag_results, top_k=top_k)
    return {"retrieval": retrieval_metrics, "judge": judge_metrics}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P2-2 RAGAS-style LLM-as-Judge eval for AI_Shop RAG."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("rag_golden.jsonl"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-faithfulness", type=float, default=0.80,
                        help="Faithfulness gate (default 0.80).")
    parser.add_argument("--min-recall", type=float, default=0.80,
                        help="Retrieval recallAtK gate (default 0.80).")
    parser.add_argument("--no-fail", action="store_true",
                        help="Print metrics without enforcing quality gates.")
    parser.add_argument("--no-per-case", action="store_true",
                        help="Omit per-case details from JSON output.")
    args = parser.parse_args()

    metrics = asyncio.run(run(args.dataset, args.top_k))

    if args.no_per_case:
        (metrics.get("judge") or {}).pop("perCase", None)

    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    judge = metrics.get("judge") or {}
    retrieval = metrics.get("retrieval") or {}
    passed = True
    if judge.get("judgedCases", 0) > 0:
        if judge.get("faithfulness", 0.0) < args.min_faithfulness:
            print(f"FAIL faithfulness={judge['faithfulness']:.4f} < {args.min_faithfulness}",
                  flush=True)
            passed = False
    if retrieval.get("recallAtK", 0.0) < args.min_recall:
        print(f"FAIL recallAtK={retrieval['recallAtK']:.4f} < {args.min_recall}", flush=True)
        passed = False

    if not args.no_fail and not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
