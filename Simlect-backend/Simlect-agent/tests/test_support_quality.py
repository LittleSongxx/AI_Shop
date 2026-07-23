from app.services.support_service import _eligible_faq_candidate


def test_positive_public_answer_can_enter_faq_candidate_pool():
    assert _eligible_faq_candidate(
        {
            "user_message": "发票在哪里申请？",
            "assistant_message": "可在订单详情页选择申请发票，并按页面提示填写抬头信息。",
            "biz_type": "chat",
            "intent": "INVOICE",
        }
    )


def test_private_order_answer_is_not_promoted_to_faq():
    assert not _eligible_faq_candidate(
        {
            "user_message": "我的退款到哪了？",
            "assistant_message": "退款单 20260723001 正在处理中，请耐心等待。",
            "biz_type": "chat",
            "intent": "REFUND_STATUS",
        }
    )


def test_structured_card_payload_is_not_promoted_to_faq():
    assert not _eligible_faq_candidate(
        {
            "user_message": "推荐一些零食",
            "assistant_message": '[{"productId":"1","productName":"测试商品"}]',
            "biz_type": "product_search",
            "intent": "PRODUCT_SEARCH",
        }
    )
