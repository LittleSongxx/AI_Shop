from app.utils.refund_policy import asks_refund_conditions, cited_refund_conditions


def test_refund_conditions_require_every_visible_snippet_claim() -> None:
    result = cited_refund_conditions(
        [
            {
                "id": "knowledge-2",
                "heading": "退货与退款",
                "snippet": (
                    "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。"
                    "平台会根据商品类型、订单状态和实际情况审核。"
                ),
            }
        ]
    )

    assert result is not None
    assert result["citation"] == 1
    assert "包装完整。[1]" in result["answer"]
    assert "实际情况审核。[1]" in result["answer"]


def test_refund_conditions_reject_topic_only_or_partial_source() -> None:
    assert cited_refund_conditions(
        [
            {
                "factIds": ["aftersales.request_and_refund_boundary"],
                "snippet": "退款应在订单详情发起。",
            }
        ]
    ) is None
    assert asks_refund_conditions("退款需要满足哪些条件")
    assert not asks_refund_conditions("我的退款到哪一步了")
