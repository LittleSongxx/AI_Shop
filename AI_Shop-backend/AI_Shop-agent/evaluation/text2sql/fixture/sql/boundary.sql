INSERT INTO aishop_order.order_info
    (order_id, amount, goods_amount, discount_amount, user_id, order_time, order_status, subject)
VALUES
    ('O2001', 100.00, 100.00, 0.00, 'U010', '2026-08-21 18:00:00', 1, 'tie fixture');

INSERT INTO aishop_order.order_item
    (order_item_id, order_id, product_id, product_name, property_value_id_hash,
     item_amount, buy_count, order_item_status)
VALUES
    ('I2001', 'O2001', 'P200', '智能手表-下单快照', 'SKU-P200-A', 100.00, 2, 1);

INSERT INTO aishop_agent.agent_run
    (run_id, user_id, agent_id, status, outcome, intent, input_tokens, output_tokens,
     cost_cny, latency_ms, started_at, completed_at)
VALUES
    ('RUN2001', 'U010', 'inventory', 'FAILED', 'error', 'INVENTORY', 50, 5,
     0.02000000, NULL, '2026-08-27 18:00:00.000', '2026-08-27 18:00:01.000');

INSERT INTO aishop_agent.agent_step
    (run_id, event_type, status, tool_name, latency_ms, occurred_at, agent_id)
VALUES
    ('RUN2001', 'TOOL_CALL', 'ERROR', 'inventory', NULL, '2026-08-27 18:00:00.500', 'inventory');

INSERT INTO aishop_agent.agent_recommendation_event
    (client_event_id, idempotency_key, user_id, request_id, product_id, position,
     source, retrieval_mode, event_type, occurred_at)
VALUES
    ('CE2001', 'IK2001', 'U010', 'REQ2001', 'P300', 1, 'agent', 'text', 'IMPRESSION', '2026-08-27 18:00:00.000');

INSERT INTO aishop_agent.agent_final_offer_snapshot
    (snapshot_id, user_id, product_id, sku_key, offer_json, expires_at, created_at)
VALUES
    ('OS2001', 'U010', 'P300', 'SKU-P300-A', JSON_OBJECT('couponStatus', 'AVAILABLE', 'basePrice', '90.00', 'estimatedPayable', '80.00', 'inStock', true), '2026-08-27 19:00:00.000', '2026-08-27 18:00:00.000');
