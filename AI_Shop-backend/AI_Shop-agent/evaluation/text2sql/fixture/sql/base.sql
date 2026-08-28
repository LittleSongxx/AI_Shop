INSERT INTO aishop_product.product_info
    (product_id, product_name, product_desc, create_time, status, min_price, max_price, total_sale)
VALUES
    ('P100', '降噪耳机', 'Text2SQL fixture', '2026-01-01 00:00:00', 1, 50.00, 100.00, 6),
    ('P200', '智能手表', 'Text2SQL fixture', '2026-01-01 00:00:00', 1, 100.00, 300.00, 4),
    ('P300', '机械键盘', 'Text2SQL fixture', '2026-01-01 00:00:00', 1, 30.00, 120.00, 5),
    ('P999', '已删除商品', 'must be excluded', '2026-01-01 00:00:00', -1, 1.00, 1.00, 0);

INSERT INTO aishop_stock.sku_stock (product_id, property_value_id_hash, stock)
VALUES
    ('P100', 'SKU-P100-A', 0),
    ('P100', 'SKU-P100-B', 10),
    ('P200', 'SKU-P200-A', 1),
    ('P300', 'SKU-P300-A', 50),
    ('P999', 'SKU-P999-A', 0);

INSERT INTO aishop_order.order_info
    (order_id, amount, goods_amount, discount_amount, user_id, order_time, order_status, subject)
VALUES
    ('O1001', 100.00, 100.00, 0.00, 'U001', '2026-08-21 09:00:00', 3, 'P100 order'),
    ('O1002', 200.00, 200.00, 0.00, 'U002', '2026-08-21 10:00:00', 2, 'P200 order'),
    ('O1003', 150.00, 150.00, 0.00, 'U003', '2026-08-22 11:00:00', 1, 'P100 order'),
    ('O1004', 80.00, 80.00, 0.00, 'U004', '2026-08-23 12:00:00', 4, 'cancelled'),
    ('O1005', 120.00, 120.00, 0.00, 'U005', '2026-08-24 13:00:00', 6, 'refunded'),
    ('O1006', 300.00, 300.00, 0.00, 'U006', '2026-08-25 14:00:00', 7, 'partially refunded'),
    ('O1007', 100.00, 100.00, 0.00, 'U007', '2026-08-25 15:00:00', 8, 'awaiting review'),
    ('O1008', 60.00, 60.00, 0.00, 'U008', '2026-08-26 16:00:00', 5, 'closed'),
    ('O1009', 90.00, 90.00, 0.00, 'U009', '2026-08-27 17:00:00', 1, 'P300 order');

INSERT INTO aishop_order.order_item
    (order_item_id, order_id, product_id, product_name, property_value_id_hash,
     item_amount, buy_count, order_item_status)
VALUES
    ('I1001', 'O1001', 'P100', '降噪耳机-下单快照', 'SKU-P100-A', 100.00, 2, 1),
    ('I1002', 'O1002', 'P200', '智能手表-下单快照', 'SKU-P200-A', 200.00, 1, 1),
    ('I1003', 'O1003', 'P100', '降噪耳机-下单快照', 'SKU-P100-A', 150.00, 3, 1),
    ('I1004', 'O1004', 'P300', '机械键盘-下单快照', 'SKU-P300-A', 80.00, 1, 1),
    ('I1005', 'O1005', 'P300', '机械键盘-下单快照', 'SKU-P300-A', 120.00, 2, 0),
    ('I1006', 'O1006', 'P200', '智能手表-下单快照', 'SKU-P200-A', 300.00, 3, 1),
    ('I1007', 'O1007', 'P100', '降噪耳机-下单快照', 'SKU-P100-A', 100.00, 1, 1),
    ('I1008', 'O1008', 'P200', '智能手表-下单快照', 'SKU-P200-A', 60.00, 1, 1),
    ('I1009', 'O1009', 'P300', '机械键盘-下单快照', 'SKU-P300-A', 90.00, 3, 1);

INSERT INTO aishop_order.refund_request
    (refund_request_id, refund_order_no, source_pay_order_id, order_id, order_item_id,
     user_id, product_id, property_value_id_hash, buy_count, refund_amount, status,
     created_at, updated_at, completed_at)
VALUES
    ('RR1001', 'RNO1001', 'PAY1001', 'O1005', 'I1005', 'U005', 'P300', 'SKU-P300-A', 2,
     120.00, 'COMPLETED', '2026-08-24 13:30:00', '2026-08-26 09:00:00', '2026-08-26 09:00:00'),
    ('RR1002', 'RNO1002', 'PAY1002', 'O1006', 'I1006', 'U006', 'P200', 'SKU-P200-A', 1,
     100.00, 'COMPLETED', '2026-08-25 14:30:00', '2026-08-27 10:00:00', '2026-08-27 10:00:00'),
    ('RR1003', 'RNO1003', 'PAY1003', 'O1004', 'I1004', 'U004', 'P300', 'SKU-P300-A', 1,
     80.00, 'PENDING_PAYMENT', '2026-08-23 12:30:00', '2026-08-23 12:30:00', NULL);

INSERT INTO aishop_agent.agent_run
    (run_id, user_id, agent_id, status, outcome, intent, input_tokens, output_tokens,
     cost_cny, latency_ms, started_at, completed_at)
VALUES
    ('RUN1001', 'U001', 'shopper', 'SUCCEEDED', 'answer', 'RECOMMENDATION', 100, 40,
     0.12345678, 100, '2026-08-21 09:00:00.000', '2026-08-21 09:00:00.100'),
    ('RUN1002', 'U002', 'shopper', 'FAILED', 'error', 'RECOMMENDATION', 80, 10,
     0.08000000, 300, '2026-08-21 10:00:00.000', '2026-08-21 10:00:00.300'),
    ('RUN1003', 'U003', 'support', 'HANDOFF', 'human_support', 'AFTER_SALES', 60, 20,
     0.05000000, 200, '2026-08-22 11:00:00.000', '2026-08-22 11:00:00.200'),
    ('RUN1004', 'U004', 'inventory', 'SUCCEEDED', 'answer', 'INVENTORY', 90, 30,
     0.10000000, 150, '2026-08-25 14:00:00.000', '2026-08-25 14:00:00.150'),
    ('RUN1005', 'U005', 'shopper', 'SUCCEEDED', 'answer', 'RECOMMENDATION', 110, 50,
     0.20000000, 120, '2026-08-27 17:00:00.000', '2026-08-27 17:00:00.120');

INSERT INTO aishop_agent.agent_step
    (run_id, event_type, status, tool_name, latency_ms, occurred_at, agent_id)
VALUES
    ('RUN1001', 'TOOL_CALL', 'OK', 'catalog_search', 40, '2026-08-21 09:00:00.040', 'shopper'),
    ('RUN1001', 'TOOL_CALL', 'SUCCESS', 'inventory', 20, '2026-08-21 09:00:00.070', 'shopper'),
    ('RUN1002', 'TOOL_CALL', 'ERROR', 'catalog_search', 250, '2026-08-21 10:00:00.250', 'shopper'),
    ('RUN1003', 'TOOL_CALL', 'OK', 'order_lookup', 80, '2026-08-22 11:00:00.080', 'support'),
    ('RUN1004', 'TOOL_CALL', 'OK', 'inventory', 70, '2026-08-25 14:00:00.070', 'inventory'),
    ('RUN1005', 'TOOL_CALL', 'ERROR', 'inventory', 100, '2026-08-27 17:00:00.100', 'shopper');

INSERT INTO aishop_agent.agent_recommendation_event
    (client_event_id, idempotency_key, user_id, request_id, product_id, position,
     source, retrieval_mode, event_type, occurred_at)
VALUES
    ('CE1001', 'IK1001', 'U001', 'REQ1001', 'P100', 1, 'agent', 'text', 'IMPRESSION', '2026-08-21 09:00:00.000'),
    ('CE1002', 'IK1002', 'U001', 'REQ1001', 'P100', 1, 'agent', 'text', 'CLICK', '2026-08-21 09:01:00.000'),
    ('CE1003', 'IK1003', 'U002', 'REQ1002', 'P200', 1, 'agent', 'text', 'IMPRESSION', '2026-08-21 10:00:00.000'),
    ('CE1004', 'IK1004', 'U003', 'REQ1003', 'P200', 1, 'agent', 'visual', 'IMPRESSION', '2026-08-22 11:00:00.000'),
    ('CE1005', 'IK1005', 'U003', 'REQ1003', 'P200', 1, 'agent', 'visual', 'CLICK', '2026-08-22 11:01:00.000');

INSERT INTO aishop_agent.commerce_outcome_ledger
    (event_id, source, idempotency_key, event_type, user_id, request_id, product_id,
     payload_json, occurred_at)
VALUES
    ('OE1001', 'fixture', 'OIK1001', 'ADD_TO_CART', 'U001', 'REQ1001', 'P100',
     JSON_OBJECT('attributionStatus', 'VERIFIED', 'attribution', JSON_OBJECT('retrievalMode', 'text')), '2026-08-22 09:00:00.000'),
    ('OE1002', 'fixture', 'OIK1002', 'PAYMENT', 'U001', 'REQ1001', 'P100',
     JSON_OBJECT('attributionStatus', 'VERIFIED', 'attribution', JSON_OBJECT('retrievalMode', 'text')), '2026-08-23 09:00:00.000'),
    ('OE1003', 'fixture', 'OIK1003', 'PAYMENT', 'U003', 'REQ1003', 'P200',
     JSON_OBJECT('attributionStatus', 'VERIFIED', 'attribution', JSON_OBJECT('retrievalMode', 'visual')), '2026-08-22 12:00:00.000'),
    ('OE1004', 'fixture', 'OIK1004', 'REFUND', 'U001', 'REQ1001', 'P100',
     JSON_OBJECT('attributionStatus', 'VERIFIED'), '2026-08-26 09:00:00.000'),
    ('OE1005', 'fixture', 'OIK1005', 'RETURN', 'U003', 'REQ1003', 'P200',
     JSON_OBJECT('attributionStatus', 'VERIFIED'), '2026-08-27 09:00:00.000'),
    ('OE1006', 'fixture', 'OIK1006', 'REVIEW', 'U001', 'REQ1001', 'P100',
     JSON_OBJECT('attributionStatus', 'VERIFIED', 'rating', 2), '2026-08-27 10:00:00.000'),
    ('OE1007', 'fixture', 'OIK1007', 'SUPPORT_CONTACT', 'U001', 'REQ1001', 'P100',
     JSON_OBJECT('attributionStatus', 'VERIFIED'), '2026-08-27 11:00:00.000'),
    ('OE1008', 'fixture', 'OIK1008', 'REPEAT_PURCHASE', 'U003', 'REQ1003', 'P200',
     JSON_OBJECT('attributionStatus', 'VERIFIED'), '2026-08-27 12:00:00.000'),
    ('OE1009', 'fixture', 'OIK1009', 'PAYMENT', 'U004', 'REQ9999', 'P300',
     JSON_OBJECT('attributionStatus', 'UNVERIFIED', 'attribution', JSON_OBJECT('retrievalMode', 'text')), '2026-08-27 13:00:00.000');

INSERT INTO aishop_agent.agent_final_offer_snapshot
    (snapshot_id, user_id, product_id, sku_key, offer_json, expires_at, created_at)
VALUES
    ('OS1001', 'U001', 'P100', 'SKU-P100-A', JSON_OBJECT('couponStatus', 'AVAILABLE', 'basePrice', '100.00', 'estimatedPayable', '90.00', 'inStock', true), '2026-08-21 10:00:00.000', '2026-08-21 09:00:00.000'),
    ('OS1002', 'U002', 'P100', 'SKU-P100-B', JSON_OBJECT('couponStatus', 'UNAVAILABLE', 'basePrice', '100.00', 'estimatedPayable', '100.00', 'inStock', false), '2026-08-21 11:00:00.000', '2026-08-21 10:00:00.000'),
    ('OS1003', 'U003', 'P200', 'SKU-P200-A', JSON_OBJECT('couponStatus', 'AVAILABLE', 'basePrice', '200.00', 'estimatedPayable', '180.00', 'inStock', true), '2026-08-22 12:00:00.000', '2026-08-22 11:00:00.000'),
    ('OS1004', 'U004', 'P300', 'SKU-P300-A', JSON_OBJECT('couponStatus', 'UNAVAILABLE', 'basePrice', '90.00', 'estimatedPayable', '90.00', 'inStock', true), '2026-08-27 18:00:00.000', '2026-08-27 17:00:00.000');

INSERT INTO aishop_agent.agent_inventory_supply_parameter
    (product_id, sku_key, supplier_ref, lead_time_days, safety_stock,
     min_order_quantity, review_period_days, enabled, updated_by)
VALUES
    ('P100', 'SKU-P100-A', 'SUP-A', 7, 5, 10, 14, 1, 'fixture'),
    ('P100', 'SKU-P100-B', 'SUP-B', 7, 2, 5, 14, 1, 'fixture'),
    ('P200', 'SKU-P200-A', 'SUP-C', 10, 4, 6, 14, 1, 'fixture'),
    ('P300', 'SKU-P300-A', 'SUP-D', 5, 3, 5, 7, 1, 'fixture');

INSERT INTO aishop_agent.agent_inventory_inbound
    (inbound_id, product_id, sku_key, quantity, eta_date, status, source_ref)
VALUES
    ('IN1001', 'P100', 'SKU-P100-A', 1, '2026-08-29', 'PLANNED', 'fixture'),
    ('IN1002', 'P200', 'SKU-P200-A', 3, '2026-08-28', 'IN_TRANSIT', 'fixture'),
    ('IN1003', 'P300', 'SKU-P300-A', 20, '2026-08-20', 'RECEIVED', 'ignored');
