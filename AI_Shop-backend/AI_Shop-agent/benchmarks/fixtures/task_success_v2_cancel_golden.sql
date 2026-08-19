-- Isolated local-live fixture for live-cancel-confirmed-012.
-- This file contains no login token. Restore it immediately before the case run.
SET NAMES utf8mb4;
START TRANSACTION;

INSERT INTO aishop_order.order_info
    (order_id, amount, goods_amount, discount_amount, coupon_discount,
     user_coupon_id, coupon_id, user_id, order_time, order_status,
     pay_channel, pay_scene, pay_order_id, channel_order_Id,
     comment_status, subject)
VALUES
    ('SM202608200012', 24.00, 24.00, 0.00, 0.00,
     NULL, NULL, '9000000001', NOW(), 0,
     NULL, NULL, NULL, NULL,
     0, 'Agent v2 独立取消订单评测')
ON DUPLICATE KEY UPDATE
    amount = VALUES(amount),
    goods_amount = VALUES(goods_amount),
    discount_amount = VALUES(discount_amount),
    coupon_discount = VALUES(coupon_discount),
    user_coupon_id = NULL,
    coupon_id = NULL,
    user_id = VALUES(user_id),
    order_time = VALUES(order_time),
    order_status = VALUES(order_status),
    pay_channel = NULL,
    pay_scene = NULL,
    pay_order_id = NULL,
    channel_order_Id = NULL,
    comment_status = 0,
    subject = VALUES(subject);

INSERT INTO aishop_order.order_item
    (order_item_id, order_id, cover, product_id, product_name,
     property_value_id_hash, property_info, item_amount, buy_count,
     order_item_status, remark, refund_order_id)
VALUES
    ('SMITEM202608200012', 'SM202608200012',
     '2026-06/QI7QDtLaR1mVCTtmcIbfywOhqBN0Ew.jpg',
     '065293686460191', '旺旺 雪饼 厚烧海苔 原味 385g',
     'e8fe120e5a424353c0b6053ff1d88876',
     '厚烧海苔分享装 385g*1袋', 24.00, 1, 1,
     'Agent v2 独立取消订单评测', NULL)
ON DUPLICATE KEY UPDATE
    order_id = VALUES(order_id),
    cover = VALUES(cover),
    product_id = VALUES(product_id),
    product_name = VALUES(product_name),
    property_value_id_hash = VALUES(property_value_id_hash),
    property_info = VALUES(property_info),
    item_amount = VALUES(item_amount),
    buy_count = VALUES(buy_count),
    order_item_status = VALUES(order_item_status),
    remark = VALUES(remark),
    refund_order_id = NULL;

COMMIT;
