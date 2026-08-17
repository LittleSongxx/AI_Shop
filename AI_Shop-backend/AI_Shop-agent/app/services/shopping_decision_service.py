"""User-benefit-first ranking over verified offers and product facts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.episode_service import episode_service
from app.services.final_offer_snapshot_service import (
    OfferSnapshotUnavailable,
    final_offer_snapshot_service,
)
from app.services.product_decision_feature_service import product_decision_feature_service
from app.services.shopping_mission_service import mission_summary, schema_for
from app.services.shopping_profile_service import shopping_profile_service

logger = structlog.get_logger()

POLICY_VERSION = "shopping-user-utility-v2"

_USE_CASE_TERMS: dict[str, tuple[str, ...]] = {
    "编程开发": ("编程", "开发", "处理器", "cpu", "内存", "键盘"),
    "视频创作": ("视频", "剪辑", "创作", "显卡", "gpu", "存储", "屏幕"),
    "游戏娱乐": ("游戏", "显卡", "gpu", "高刷", "性能"),
    "日常办公": ("办公", "轻薄", "续航", "便携", "键盘"),
    "拍照影像": ("拍照", "影像", "相机", "镜头"),
    "长续航": ("续航", "电池", "电量"),
    "通勤降噪": ("通勤", "降噪", "anc", "续航"),
    "音乐欣赏": ("音质", "hifi", "动圈", "音频"),
    "上学通勤": ("轻", "容量", "收纳", "电脑"),
    "上班通勤": ("轻", "容量", "收纳", "电脑"),
    "旅行出差": ("容量", "收纳", "旅行", "防水"),
    "户外运动": ("防水", "耐磨", "轻", "容量"),
    "运动": ("防水", "运动", "轻"),
}


@dataclass(frozen=True)
class ShoppingDecisionResult:
    products: list[dict[str, Any]]
    source: str
    request_id: str
    decision_id: str | None = None
    warning: str | None = None


def _text(product: dict[str, Any]) -> str:
    values = [
        product.get("product_name"),
        product.get("productName"),
        product.get("brand"),
    ]
    for feature in product.get("decisionFeatures") or []:
        if isinstance(feature, dict):
            values.extend((feature.get("key"), feature.get("value")))
    return " ".join(str(value or "") for value in values).lower()


def _feature_values(product: dict[str, Any]) -> list[str]:
    return [
        str(feature.get("value") or "")
        for feature in product.get("decisionFeatures") or []
        if isinstance(feature, dict) and str(feature.get("reviewStatus") or "") == "VERIFIED"
    ]


def _fraction(matches: int, total: int, default: float = 0.5) -> float:
    if total <= 0:
        return default
    return min(1.0, max(0.0, matches / total))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _future_timestamp(value: Any) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)


class ShoppingDecisionService:
    async def decide(
        self,
        *,
        user_id: str,
        mission: dict[str, Any],
        candidates: list[dict[str, Any]],
        source: str,
        request_id: str | None = None,
        user_text: str = "",
    ) -> ShoppingDecisionResult:
        request_id = request_id or uuid.uuid4().hex
        if not candidates:
            await self._record_decision(
                user_id, mission, request_id, [], [], source, status="NO_CANDIDATES"
            )
            return ShoppingDecisionResult([], "constraint_miss", request_id)

        annotated = await product_decision_feature_service.annotate_candidates(candidates)
        episode_service.record_step(
            "PRODUCT_FACTS_VERIFIED",
            node_name="shopping_decision",
            output_data={
                "candidateCount": len(annotated),
                "verifiedFeatureCount": sum(
                    len(
                        [
                            feature
                            for feature in product.get("decisionFeatures") or []
                            if isinstance(feature, dict)
                            and feature.get("reviewStatus") == "VERIFIED"
                        ]
                    )
                    for product in annotated
                ),
                "mission": mission_summary(mission),
            },
            agent_id="shopping_advisor",
        )
        try:
            offered = await final_offer_snapshot_service.build(user_id, annotated)
        except OfferSnapshotUnavailable:
            episode_service.record_step(
                "OFFER_SNAPSHOT_UNAVAILABLE",
                node_name="shopping_decision",
                status="DEGRADED",
                output_data={"candidateCount": len(annotated)},
                agent_id="shopping_advisor",
            )
            return ShoppingDecisionResult(
                [], "offer_unavailable", request_id, warning="当前无法核验实时价格和库存"
            )
        episode_service.record_step(
            "OFFER_SNAPSHOT_CREATED",
            node_name="shopping_decision",
            output_data={
                "verifiedOfferCount": len(offered),
                "couponVerifiedCount": sum(
                    product.get("coupon_status") == "AVAILABLE" for product in offered
                ),
                "offers": [
                    {
                        "productId": product.get("product_id"),
                        "skuKey": product.get("sku_key"),
                        "snapshotId": product.get("offer_snapshot_id"),
                        "estimatedPayable": product.get("estimated_payable"),
                        "couponStatus": product.get("coupon_status"),
                        "expiresAt": product.get("quote_expires_at"),
                    }
                    for product in offered[:12]
                ],
            },
            agent_id="shopping_advisor",
        )

        eligible, rejected = self._hard_filter(offered, mission)
        if not eligible:
            decision_id = await self._record_decision(
                user_id, mission, request_id, [], rejected, source, status="NO_ELIGIBLE_OFFER"
            )
            return ShoppingDecisionResult([], "constraint_miss", request_id, decision_id)

        ranked = self._rank(eligible, mission)
        ranked = self._apply_operational_governance(ranked, mission, user_text)
        max_results = get_settings().shopping_decision_max_results
        ranked = ranked[:max_results]
        for position, product in enumerate(ranked, start=1):
            product["position"] = position
            product["recommendation"] = self._explanation(product, mission, position)
            product["_recommend_reason"] = str(product["recommendation"]["summary"])
            product["request_id"] = request_id
        decision_id = await self._record_decision(
            user_id, mission, request_id, ranked, rejected, source, status="SUCCEEDED"
        )
        for product in ranked:
            product["ranking_decision_id"] = decision_id
        episode_service.record_step(
            "RANKING_POLICY_DECISION",
            node_name="shopping_decision",
            output_data={
                "decisionId": decision_id,
                "selectedCount": len(ranked),
                "rejectedCount": len(rejected),
                "rejectedReasons": {
                    reason: sum(1 for item in rejected if item.get("reason") == reason)
                    for reason in sorted({str(item.get("reason") or "UNKNOWN") for item in rejected})
                },
                "selected": [
                    {
                        "productId": product.get("product_id") or product.get("productId"),
                        "position": product.get("position"),
                        "role": (product.get("recommendation") or {}).get("role"),
                        "offerSnapshotId": product.get("offer_snapshot_id"),
                        "quoteExpiresAt": product.get("quote_expires_at"),
                        "estimatedPayable": product.get("estimated_payable"),
                        "operationRecommended": bool(product.get("operation_recommended")),
                    }
                    for product in ranked
                ],
                "operationalCount": sum(bool(product.get("operation_recommended")) for product in ranked),
                "policyVersion": POLICY_VERSION,
                "mission": mission_summary(mission),
            },
            agent_id="shopping_advisor",
            artifact_type="ShoppingDecisionArtifact",
        )
        return ShoppingDecisionResult(ranked, "shopping_decision_v2", request_id, decision_id)

    def _hard_filter(
        self, products: list[dict[str, Any]], mission: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        hard = mission.get("hardConstraints") or {}
        soft = mission.get("softPreferences") or {}
        exclusions = mission.get("exclusions") or {}
        min_budget = _number(hard.get("budgetMin"))
        max_budget = _number(hard.get("budgetMax"))
        required_brands = {str(value).lower() for value in hard.get("requiredBrands") or []}
        excluded_brands = {str(value).lower() for value in exclusions.get("brands") or []}
        brand_profile = {
            "brands": list(hard.get("requiredBrands") or soft.get("brands") or []),
            "excludedBrands": list(exclusions.get("brands") or []),
        }
        eligible: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        for raw in products:
            product = dict(raw)
            product_id = str(product.get("product_id") or product.get("productId") or "")
            price = _number(product.get("estimated_payable"))
            brand = str(
                shopping_profile_service.resolve_known_brand(product, brand_profile) or ""
            ).lower()
            if brand:
                product["brand"] = brand
            reason = None
            if str(product.get("status")) != "1" or product.get("in_stock") is False:
                reason = "NOT_PURCHASABLE"
            elif not str(product.get("offer_snapshot_id") or "").strip():
                reason = "OFFER_SNAPSHOT_MISSING"
            elif not _future_timestamp(product.get("quote_expires_at")):
                reason = "QUOTE_EXPIRED"
            elif price is None:
                reason = "QUOTE_MISSING"
            elif max_budget is not None and price > max_budget:
                reason = "OVER_BUDGET"
            elif min_budget is not None and price < min_budget:
                reason = "BELOW_BUDGET_RANGE"
            elif required_brands and brand not in required_brands:
                reason = "BRAND_REQUIRED"
            elif brand and brand in excluded_brands:
                reason = "BRAND_EXCLUDED"
            if reason:
                rejected.append({"productId": product_id, "reason": reason})
                continue
            product["_mission_brand_preferred"] = bool(
                brand and brand in {str(value).lower() for value in soft.get("brands") or []}
            )
            eligible.append(product)
        return eligible, rejected

    def _rank(self, products: list[dict[str, Any]], mission: dict[str, Any]) -> list[dict[str, Any]]:
        schema = schema_for(str(mission.get("category") or ""))
        weights = schema["weights"]
        hard = mission.get("hardConstraints") or {}
        soft = mission.get("softPreferences") or {}
        use_cases = [str(value) for value in mission.get("useCases") or []]
        preferred_features = [str(value).lower() for value in soft.get("features") or []]
        max_budget = _number(hard.get("budgetMax"))
        personalization = mission.get("personalization") or {}
        implicit_signals = (
            personalization.get("implicitSignals") or []
            if personalization.get("enabled", True)
            else []
        )
        ranked: list[dict[str, Any]] = []
        for recall_rank, raw in enumerate(products, start=1):
            product = dict(raw)
            text = _text(product)
            use_case_terms = [term for use_case in use_cases for term in _USE_CASE_TERMS.get(use_case, (use_case.lower(),))]
            use_case_score = _fraction(sum(term.lower() in text for term in use_case_terms), len(use_case_terms))
            feature_score = _fraction(
                sum(feature in text for feature in preferred_features), len(preferred_features)
            )
            payable = _number(product.get("estimated_payable")) or 0.0
            offer_score = 0.5
            if max_budget and max_budget > 0:
                offer_score = min(1.0, max(0.0, 1 - payable / max_budget + 0.25))
            explicit_score = 1.0 if product.get("_mission_brand_preferred") else 0.5
            # Inferred behaviour is intentionally a small, explainable nudge.
            # It cannot become a hard filter and is ignored when personalization
            # is disabled; current-turn mission constraints remain authoritative.
            inferred_adjustment = 0.0
            product_key = str(
                product.get("product_id") or product.get("productId") or ""
            ).lower()
            product_text = text
            for signal in implicit_signals[:20]:
                if not isinstance(signal, dict):
                    continue
                value = str(signal.get("value") or "").lower()
                try:
                    weight = min(1.0, max(0.0, float(signal.get("effectiveWeight") or 0)))
                except (TypeError, ValueError):
                    weight = 0.0
                if not value or weight <= 0:
                    continue
                kind = str(signal.get("kind") or "")
                normalized_kind = kind.casefold()
                matches = (
                    value == product_key
                    if normalized_kind in {"product", "negativeproduct"}
                    else value in product_text
                )
                if matches:
                    inferred_adjustment += (
                        0.15 if not normalized_kind.startswith("negative") else -0.15
                    ) * weight
            explicit_score = min(1.0, max(0.0, explicit_score + inferred_adjustment))
            diversity_score = 1.0 / recall_rank
            score = (
                weights["useCase"] * use_case_score
                + weights["feature"] * feature_score
                + weights["offer"] * offer_score
                + weights["explicit"] * explicit_score
                + weights["diversity"] * diversity_score
            )
            product["ranking"] = {
                "utilityScore": round(score, 4),
                "useCaseScore": round(use_case_score, 4),
                "featureScore": round(feature_score, 4),
                "offerScore": round(offer_score, 4),
                "explicitPreferenceScore": round(explicit_score, 4),
                "diversityScore": round(diversity_score, 4),
                "policyVersion": POLICY_VERSION,
            }
            ranked.append(product)
        return sorted(
            ranked,
            key=lambda product: (-float(product["ranking"]["utilityScore"]), str(product.get("product_id") or "")),
        )

    def _apply_operational_governance(
        self,
        ranked: list[dict[str, Any]],
        mission: dict[str, Any],
        user_text: str,
    ) -> list[dict[str, Any]]:
        if not get_settings().operational_recommendations_enabled or len(ranked) < 5:
            return ranked
        if any(marker in user_text for marker in ("最适合", "最推荐", "最好", "只要最")):
            return ranked
        cutoff = float(ranked[min(4, len(ranked) - 1)]["ranking"]["utilityScore"])
        candidate_index = next(
            (
                index
                for index, product in enumerate(ranked[1:], start=1)
                if int(product.get("commend_type") or product.get("commendType") or 0) > 0
                and float(product["ranking"]["utilityScore"]) >= cutoff * 0.95
            ),
            None,
        )
        if candidate_index is None:
            return ranked
        candidate = ranked.pop(candidate_index)
        target_index = min(1, len(ranked))
        ranked.insert(target_index, candidate)
        candidate["operation_recommended"] = True
        candidate["commercialDisclosure"] = "运营推荐"
        candidate["ranking"]["operationalPlacement"] = target_index + 1
        return ranked

    def _explanation(
        self, product: dict[str, Any], mission: dict[str, Any], position: int) -> dict[str, Any]:
        features = product.get("decisionFeatures") or []
        evidence = product_decision_feature_service.evidence_for(features)
        use_cases = [str(value) for value in mission.get("useCases") or []]
        price = _number(product.get("estimated_payable"))
        role = "用途匹配优先"
        text = _text(product)
        if any(token in text for token in ("性能", "cpu", "显卡", "gpu")):
            role = "性能优先"
        elif any(token in text for token in ("续航", "电池")):
            role = "续航优先"
        elif any(token in text for token in ("轻薄", "重量", "便携")):
            role = "便携优先"
        elif position > 1 and price is not None:
            role = "性价比优先"
        suitable = (
            f"更适合{'、'.join(use_cases)}等当前用途"
            if use_cases and evidence
            else "已通过当前预算、在售和库存条件，可作为对比候选"
        )
        tradeoff = (
            "关键用途属性已有商品结构化证据，可在详情页继续确认具体 SKU。"
            if evidence
            else "没有足够的已验证商品属性支撑更强结论，请在详情页确认关键规格。"
        )
        return {
            "role": role,
            "summary": f"{role}：{suitable}",
            "bestFor": suitable,
            "notIdealFor": tradeoff if not evidence else "若你的关键规格与已核验属性不一致，建议先比较后再下单。",
            "tradeoff": tradeoff,
            "evidence": evidence,
            "offerSnapshotId": product.get("offer_snapshot_id"),
            "quoteExpiresAt": product.get("quote_expires_at"),
        }

    async def _record_decision(
        self,
        user_id: str,
        mission: dict[str, Any],
        request_id: str,
        selected: list[dict[str, Any]],
        rejected: list[dict[str, Any]],
        source: str,
        *,
        status: str,
    ) -> str:
        decision_id = f"rank_{uuid.uuid4().hex}"
        payload = {
            "status": status,
            "policyVersion": POLICY_VERSION,
            "missionId": mission.get("missionId"),
            "missionSummary": mission_summary(mission),
            "source": source,
            "selected": [
                {
                    "productId": product.get("product_id") or product.get("productId"),
                    "snapshotId": product.get("offer_snapshot_id"),
                    "ranking": product.get("ranking"),
                    "explanation": product.get("recommendation"),
                    "factSources": product.get("recommendation", {}).get("evidence")
                    if isinstance(product.get("recommendation"), dict)
                    else [],
                    "operationRecommended": bool(product.get("operation_recommended")),
                }
                for product in selected
            ],
            "rejected": rejected[:40],
        }
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO agent_ranking_policy_decision
                        (decision_id,request_id,mission_id,user_id,policy_version,decision_json,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,NOW(3))
                    """,
                    (
                        decision_id,
                        request_id,
                        mission.get("missionId"),
                        user_id,
                        POLICY_VERSION,
                        json.dumps(payload, ensure_ascii=False, default=str),
                    ),
                )
                for position, product in enumerate(selected, start=1):
                    explanation = product.get("recommendation") or self._explanation(product, mission, position)
                    await cur.execute(
                        """
                        INSERT INTO agent_recommendation_explanation
                            (decision_id,product_id,position,explanation_json,created_at)
                        VALUES (%s,%s,%s,%s,NOW(3))
                        """,
                        (
                            decision_id,
                            str(product.get("product_id") or product.get("productId") or ""),
                            position,
                            json.dumps(explanation, ensure_ascii=False, default=str),
                        ),
                    )
        except Exception as exc:
            logger.warning("ranking_policy_persist_failed", request_id=request_id, error=type(exc).__name__)
        return decision_id


shopping_decision_service = ShoppingDecisionService()
