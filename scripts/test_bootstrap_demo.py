from scripts.bootstrap_demo import AI_DEMO_MESSAGE, find_demo_ai_message, normalize_agent_message


def test_normalize_agent_message_matches_agent_input_guard() -> None:
    assert normalize_agent_message("  商品，\u200b 推荐\t测试  ") == "商品, 推荐 测试"


def test_find_demo_ai_message_accepts_nfkc_stored_punctuation() -> None:
    stored = normalize_agent_message(AI_DEMO_MESSAGE)
    row = {"messageId": 42, "userMessage": stored, "assistantMessage": "done"}

    assert find_demo_ai_message([row]) is row
