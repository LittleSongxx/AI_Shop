-- Idempotent local demo data for Smarlect.
-- This intentionally excludes real payment, email, image moderation and map calls.
SET NAMES utf8mb4;
START TRANSACTION;

-- Demo users. The legacy MD5 value is upgraded to BCrypt by the normal login flow.
INSERT INTO aishop_user.user_info
    (user_id, nick_name, avatar, email, password, sex, join_time, last_login_time, last_login_ip, status)
VALUES
    ('9000000001', 'Smarlect体验官', NULL, 'demo@smarlect.local',
     'fd6138204c4eb1dd19e63896c1557e27', 2, NOW(), NOW(), '127.0.0.1', 1),
    ('9000000002', 'Smarlect买手', NULL, 'buyer@smarlect.local',
     'fd6138204c4eb1dd19e63896c1557e27', 1, DATE_SUB(NOW(), INTERVAL 1 DAY),
     DATE_SUB(NOW(), INTERVAL 1 DAY), '127.0.0.1', 1)
ON DUPLICATE KEY UPDATE
    nick_name = VALUES(nick_name), email = VALUES(email), password = VALUES(password),
    sex = VALUES(sex), last_login_ip = VALUES(last_login_ip), status = VALUES(status);

INSERT INTO aishop_user.user_address
    (address_id, user_id, address, addressee, phone, default_type)
VALUES
    ('SMADDR00000001', '9000000001', '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心',
     '演示用户', '13800000001', 1),
    ('SMADDR00000002', '9000000002', '上海市浦东新区张江路 100 号',
     '演示买手', '13800000002', 1)
ON DUPLICATE KEY UPDATE
    address = VALUES(address), addressee = VALUES(addressee), phone = VALUES(phone),
    default_type = VALUES(default_type);

INSERT INTO aishop_user.user_member_profile
    (user_id, level_code, growth_value, level_name, update_time)
VALUES
    ('9000000001', 2, 3260, '银卡会员', NOW()),
    ('9000000002', 1, 360, '普通会员', NOW())
ON DUPLICATE KEY UPDATE
    level_code = VALUES(level_code), growth_value = VALUES(growth_value),
    level_name = VALUES(level_name), update_time = VALUES(update_time);

INSERT INTO aishop_user.user_sign_record
    (user_id, continuous_days, total_sign_days, used_count)
VALUES ('9000000001', 6, 18, 1)
ON DUPLICATE KEY UPDATE
    continuous_days = VALUES(continuous_days), total_sign_days = VALUES(total_sign_days),
    used_count = VALUES(used_count);

INSERT INTO aishop_user.user_sign_record_detail
    (user_id, sign_date, sign_type, create_time)
VALUES
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 DAY), '%Y%m%d'), 0, DATE_SUB(NOW(), INTERVAL 6 DAY)),
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 5 DAY), '%Y%m%d'), 0, DATE_SUB(NOW(), INTERVAL 5 DAY)),
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 4 DAY), '%Y%m%d'), 1, DATE_SUB(NOW(), INTERVAL 4 DAY)),
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), '%Y%m%d'), 0, DATE_SUB(NOW(), INTERVAL 3 DAY)),
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), '%Y%m%d'), 0, DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('9000000001', DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%Y%m%d'), 0, DATE_SUB(NOW(), INTERVAL 1 DAY))
ON DUPLICATE KEY UPDATE sign_type = VALUES(sign_type), create_time = VALUES(create_time);

INSERT INTO aishop_user.user_notification
    (notification_id, user_id, title, content, biz_type, biz_id, read_status, create_time)
VALUES
    ('SMNOTICE00000000000000000000001', '9000000001', '欢迎来到 Smarlect',
     '演示账号已准备完成，可以体验选购、收藏、订单、会员与 AI 助手。',
     'system', 'WELCOME', 0, NOW()),
    ('SMNOTICE00000000000000000000002', '9000000001', '订单已发货',
     '您的折叠屏手机订单已由顺丰速运揽收。', 'order', 'SM202608050003', 0,
     DATE_SUB(NOW(), INTERVAL 1 DAY)),
    ('SMNOTICE00000000000000000000003', '9000000001', '会员权益已更新',
     '您当前是银卡会员，可在会员中心查看升级礼。', 'member_level', '2', 1,
     DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('SMNOTICE00000000000000000000004', '9000000001', '优惠券到账',
     '一张 Smarlect 新客满减券已放入您的账户。', 'coupon', 'SM_FULL_20', 0,
     DATE_SUB(NOW(), INTERVAL 3 DAY))
ON DUPLICATE KEY UPDATE
    title = VALUES(title), content = VALUES(content), biz_type = VALUES(biz_type),
    biz_id = VALUES(biz_id), read_status = VALUES(read_status), create_time = VALUES(create_time);

INSERT INTO aishop_user.user_product_favorite
    (favorite_id, user_id, product_id, create_time)
VALUES
    ('SMFAV000000000000000000000001', '9000000001', '231335860060520', DATE_SUB(NOW(), INTERVAL 1 DAY)),
    ('SMFAV000000000000000000000002', '9000000001', '053997047858558', DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('SMFAV000000000000000000000003', '9000000001', '158081823347974', DATE_SUB(NOW(), INTERVAL 3 DAY)),
    ('SMFAV000000000000000000000004', '9000000001', '100766326868880', DATE_SUB(NOW(), INTERVAL 4 DAY))
ON DUPLICATE KEY UPDATE create_time = VALUES(create_time);

INSERT INTO aishop_user.user_browse_history
    (user_id, product_id, browse_time)
VALUES
    ('9000000001', '231335860060520', NOW()),
    ('9000000001', '053997047858558', DATE_SUB(NOW(), INTERVAL 1 HOUR)),
    ('9000000001', '158081823347974', DATE_SUB(NOW(), INTERVAL 2 HOUR)),
    ('9000000001', '100766326868880', DATE_SUB(NOW(), INTERVAL 3 HOUR)),
    ('9000000001', '065293686460191', DATE_SUB(NOW(), INTERVAL 4 HOUR)),
    ('9000000001', '088573992437392', DATE_SUB(NOW(), INTERVAL 5 HOUR))
ON DUPLICATE KEY UPDATE browse_time = VALUES(browse_time);

-- Coupon catalog and user wallet.
INSERT INTO aishop_coupon.discount_coupon
    (coupon_id, coupon_name, coupon_type, threshold_amount, discount_amount, discount_rate,
     total_count, remain_count, valid_start_time, valid_end_time, status, `rushingStatus`,
     rushing_start_time, rushing_end_time, create_time, update_time)
VALUES
    ('SM_FULL_20', 'Smarlect 新客满 100 减 20', 1, 100.00, 20.00, NULL,
     500, 486, DATE_SUB(NOW(), INTERVAL 30 DAY), DATE_ADD(NOW(), INTERVAL 180 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 30 DAY), NOW()),
    ('SM_FULL_200', '数码家电满 2000 减 200', 1, 2000.00, 200.00, NULL,
     200, 178, DATE_SUB(NOW(), INTERVAL 30 DAY), DATE_ADD(NOW(), INTERVAL 120 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 30 DAY), NOW()),
    ('SM_NO_10', '会员无门槛 10 元券', 3, 0.00, 10.00, NULL,
     1000, 940, DATE_SUB(NOW(), INTERVAL 15 DAY), DATE_ADD(NOW(), INTERVAL 90 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 15 DAY), NOW()),
    ('SM_DISCOUNT_90', '精选商品 9 折券', 2, 200.00, NULL, 0.90,
     300, 273, DATE_SUB(NOW(), INTERVAL 10 DAY), DATE_ADD(NOW(), INTERVAL 60 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 10 DAY), NOW()),
    ('SM_MEMBER_30', '银卡会员升级礼 30 元券', 3, 0.00, 30.00, NULL,
     500, 465, DATE_SUB(NOW(), INTERVAL 30 DAY), DATE_ADD(NOW(), INTERVAL 365 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 30 DAY), NOW()),
    ('SM_MEMBER_100', '金卡会员升级礼 100 元券', 3, 0.00, 100.00, NULL,
     200, 188, DATE_SUB(NOW(), INTERVAL 30 DAY), DATE_ADD(NOW(), INTERVAL 365 DAY), 1, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 30 DAY), NOW()),
    ('SM_RUSH_50', '限时抢满 500 减 50', 1, 500.00, 50.00, NULL,
     100, 76, DATE_SUB(NOW(), INTERVAL 5 DAY), DATE_ADD(NOW(), INTERVAL 30 DAY), 1, 1,
     DATE_SUB(NOW(), INTERVAL 1 DAY), DATE_ADD(NOW(), INTERVAL 7 DAY),
     DATE_SUB(NOW(), INTERVAL 5 DAY), NOW()),
    ('SM_EXPIRED_15', '已过期体验券', 3, 0.00, 15.00, NULL,
     100, 0, DATE_SUB(NOW(), INTERVAL 60 DAY), DATE_SUB(NOW(), INTERVAL 1 DAY), 2, 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 60 DAY), NOW())
ON DUPLICATE KEY UPDATE
    coupon_name = VALUES(coupon_name), coupon_type = VALUES(coupon_type),
    threshold_amount = VALUES(threshold_amount), discount_amount = VALUES(discount_amount),
    discount_rate = VALUES(discount_rate), total_count = VALUES(total_count),
    remain_count = VALUES(remain_count), valid_start_time = VALUES(valid_start_time),
    valid_end_time = VALUES(valid_end_time), status = VALUES(status),
    `rushingStatus` = VALUES(`rushingStatus`), rushing_start_time = VALUES(rushing_start_time),
    rushing_end_time = VALUES(rushing_end_time), update_time = VALUES(update_time);

INSERT INTO aishop_coupon.user_coupon
    (user_coupon_id, user_id, coupon_id, receive_time, use_time, order_id, status)
VALUES
    ('SMUC00000000000000000000000001', '9000000001', 'SM_FULL_20', NOW(), NULL, NULL, 0),
    ('SMUC00000000000000000000000002', '9000000001', 'SM_DISCOUNT_90', DATE_SUB(NOW(), INTERVAL 1 DAY), NULL, NULL, 0),
    ('SMUC00000000000000000000000003', '9000000001', 'SM_FULL_200', DATE_SUB(NOW(), INTERVAL 6 DAY),
     DATE_SUB(NOW(), INTERVAL 5 DAY), 'SM202608050002', 1),
    ('SMUC00000000000000000000000004', '9000000001', 'SM_NO_10', DATE_SUB(NOW(), INTERVAL 5 DAY),
     DATE_SUB(NOW(), INTERVAL 3 DAY), 'SM202608050005', 1),
    ('SMUC00000000000000000000000005', '9000000001', 'SM_EXPIRED_15', DATE_SUB(NOW(), INTERVAL 30 DAY),
     NULL, NULL, 2)
ON DUPLICATE KEY UPDATE
    receive_time = VALUES(receive_time), use_time = VALUES(use_time), order_id = VALUES(order_id),
    status = VALUES(status);

-- Current cart uses valid, in-stock SKUs from the bundled catalog.
INSERT INTO aishop_cart.product_cart
    (cart_id, user_id, product_id, property_value_ids, property_value_id_hash,
     buy_count, add_price, last_update_time, create_time)
VALUES
    ('SMCART00000001', '9000000001', '231335860060520',
     '17818907013950-17818907013953', '1999572e6313cccae580033af1efda51', 1, 3999.00, NOW(), DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('SMCART00000002', '9000000001', '065293686460191',
     '17819384293251', 'e8fe120e5a424353c0b6053ff1d88876', 2, 12.00, NOW(), DATE_SUB(NOW(), INTERVAL 1 DAY)),
    ('SMCART00000003', '9000000001', '088573992437392',
     '17819404064233', '2b128a73fbcc1f31046fce5291d6d008', 1, 488.00, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    buy_count = VALUES(buy_count), add_price = VALUES(add_price),
    last_update_time = VALUES(last_update_time);

-- Orders cover every non-payment demonstration state.
INSERT INTO aishop_order.order_info
    (order_id, amount, goods_amount, discount_amount, coupon_discount, user_coupon_id,
     coupon_id, user_id, order_time, order_status, pay_channel, pay_scene,
     pay_order_id, channel_order_Id, comment_status, subject)
VALUES
    ('SM202608050001', 24.00, 24.00, 0.00, 0.00, NULL, NULL, '9000000001',
     DATE_SUB(NOW(), INTERVAL 2 HOUR), 0, NULL, NULL, NULL, NULL, 0, '旺旺雪饼分享装'),
    ('SM202608050002', 3799.00, 3999.00, 200.00, 200.00,
     'SMUC00000000000000000000000003', 'SM_FULL_200', '9000000001',
     DATE_SUB(NOW(), INTERVAL 5 DAY), 1, 'alipay', 'alipay_pc', 'SMPAY202608050002',
     'SMCHANNEL202608050002', 0, '索尼 WH-1000XM6 降噪耳机'),
    ('SM202608050003', 6379.00, 6379.00, 0.00, 0.00, NULL, NULL, '9000000001',
     DATE_SUB(NOW(), INTERVAL 4 DAY), 2, 'alipay', 'alipay_pc', 'SMPAY202608050003',
     'SMCHANNEL202608050003', 0, '三星 Z Fold6 折叠屏手机'),
    ('SM202608050004', 2054.00, 2054.00, 0.00, 0.00, NULL, NULL, '9000000001',
     DATE_SUB(NOW(), INTERVAL 3 DAY), 3, 'alipay', 'alipay_pc', 'SMPAY202608050004',
     'SMCHANNEL202608050004', 1, '哈曼卡顿 AURA STUDIO 5 音箱'),
    ('SM202608050005', 14.00, 24.00, 10.00, 10.00,
     'SMUC00000000000000000000000004', 'SM_NO_10', '9000000001',
     DATE_SUB(NOW(), INTERVAL 2 DAY), 3, 'alipay', 'alipay_pc', 'SMPAY202608050005',
     'SMCHANNEL202608050005', 2, '旺旺雪饼组合装'),
    ('SM202608050006', 1430.00, 1430.00, 0.00, 0.00, NULL, NULL, '9000000001',
     DATE_SUB(NOW(), INTERVAL 1 DAY), 3, 'alipay', 'alipay_pc', 'SMPAY202608050006',
     'SMCHANNEL202608050006', 0, 'CHANEL 邂逅系列礼盒'),
    ('SM202608050007', 488.00, 488.00, 0.00, 0.00, NULL, NULL, '9000000001',
     DATE_SUB(NOW(), INTERVAL 6 DAY), 6, 'alipay', 'alipay_pc', 'SMPAY202608050007',
     'SMCHANNEL202608050007', 0, '铝合金维修工具箱')
ON DUPLICATE KEY UPDATE
    amount = VALUES(amount), goods_amount = VALUES(goods_amount),
    discount_amount = VALUES(discount_amount), coupon_discount = VALUES(coupon_discount),
    user_coupon_id = VALUES(user_coupon_id), coupon_id = VALUES(coupon_id),
    order_time = VALUES(order_time), order_status = VALUES(order_status),
    pay_channel = VALUES(pay_channel), pay_scene = VALUES(pay_scene),
    pay_order_id = VALUES(pay_order_id), channel_order_Id = VALUES(channel_order_Id),
    comment_status = VALUES(comment_status), subject = VALUES(subject);

INSERT INTO aishop_order.order_item
    (order_item_id, order_id, cover, product_id, product_name, property_value_id_hash,
     property_info, item_amount, buy_count, order_item_status, remark, refund_order_id)
VALUES
    ('SMITEM202608050001', 'SM202608050001', '2026-06/QI7QDtLaR1mVCTtmcIbfywOhqBN0Ew.jpg',
     '065293686460191', '旺旺 雪饼 厚烧海苔 原味 385g', 'e8fe120e5a424353c0b6053ff1d88876',
     '厚烧海苔分享装 385g*1袋', 24.00, 2, 1, '演示待付款订单', NULL),
    ('SMITEM202608050002', 'SM202608050002', '2026-06/RvRxT1qh1p3bwMCogWawxAEADpzlfP.jpg',
     '231335860060520', '索尼 WH-1000XM6 无线降噪耳机', '1999572e6313cccae580033af1efda51',
     '铂金银 / WH-1000XM6', 3999.00, 1, 1, '演示待发货订单', NULL),
    ('SMITEM202608050003', 'SM202608050003', '2026-06/9hIWG2qJFGVjpYLpeefUfbZYyocdXR.jpg',
     '053997047858558', '三星 Z Fold6 折叠屏手机', '3ef7e1f3c166b36043c55a6f224c435f',
     '浅玫粉 / 12+512G', 6379.00, 1, 1, '演示运输中订单', NULL),
    ('SMITEM202608050004', 'SM202608050004', '2026-06/kAZzdAvwuvXbuMOUTR87aqeL4jRbSQ.jpg',
     '158081823347974', '哈曼卡顿 AURA STUDIO 5 蓝牙音箱', '78df941091c50dea66e162da30c40eff',
     '琉璃五代', 2054.00, 1, 1, '演示已完成订单', NULL),
    ('SMITEM202608050005', 'SM202608050005', '2026-06/QI7QDtLaR1mVCTtmcIbfywOhqBN0Ew.jpg',
     '065293686460191', '旺旺 雪饼 厚烧海苔 原味 385g', 'e8fe120e5a424353c0b6053ff1d88876',
     '厚烧海苔分享装 385g*1袋', 24.00, 2, 1, '演示追评订单', NULL),
    ('SMITEM202608050006', 'SM202608050006', '2026-06/4MsbQS6FxvnI5LypKWnY9gwTqbc5zC.jpg',
     '100766326868880', 'CHANEL 香奈儿邂逅系列礼盒', 'ecaa832f282d17232a50d909fe6c37d2',
     '柔情淡香水 50ml 套装', 1430.00, 1, 1, '演示待评价订单', NULL),
    ('SMITEM202608050007', 'SM202608050007', '2026-06/MGzI6tMytQZly4G8OEg4bCM3PiIqP2.jpg',
     '088573992437392', '铝合金维修工具箱', '2b128a73fbcc1f31046fce5291d6d008',
     '铝合金 25 新型', 488.00, 1, 0, '演示已退款订单', 'SMREFUND202608050007')
ON DUPLICATE KEY UPDATE
    cover = VALUES(cover), product_name = VALUES(product_name),
    property_value_id_hash = VALUES(property_value_id_hash), property_info = VALUES(property_info),
    item_amount = VALUES(item_amount), buy_count = VALUES(buy_count),
    order_item_status = VALUES(order_item_status), remark = VALUES(remark),
    refund_order_id = VALUES(refund_order_id);

INSERT INTO aishop_order.order_coupon_rel
    (order_id, user_coupon_id, coupon_id, discount_amount, create_time)
VALUES
    ('SM202608050002', 'SMUC00000000000000000000000003', 'SM_FULL_200', 200.00, DATE_SUB(NOW(), INTERVAL 5 DAY)),
    ('SM202608050005', 'SMUC00000000000000000000000004', 'SM_NO_10', 10.00, DATE_SUB(NOW(), INTERVAL 2 DAY))
ON DUPLICATE KEY UPDATE discount_amount = VALUES(discount_amount), create_time = VALUES(create_time);

INSERT INTO aishop_order.order_comment
    (order_id, product_id, comment_content, comment_time, comment_images, star,
     comment_biz_reply, recomment_content, recomment_time, recomment_images,
     user_id, property_info, status)
VALUES
    ('SM202608050004', '158081823347974',
     '低音表现扎实，放在客厅的声场很自然，外观和商品页一致。',
     DATE_SUB(NOW(), INTERVAL 2 DAY), NULL, 5,
     '感谢您的真实反馈，祝您使用愉快。', NULL, NULL, NULL,
     '9000000001', '琉璃五代', 0),
    ('SM202608050005', '065293686460191',
     '海苔味比较均衡，包装完整，作为办公室零食很方便。',
     DATE_SUB(NOW(), INTERVAL 1 DAY), NULL, 5,
     '感谢支持 Smarlect。', '第二天口感仍然酥脆，会考虑回购。', NOW(), NULL,
     '9000000001', '厚烧海苔分享装 385g*1袋', 0)
ON DUPLICATE KEY UPDATE
    comment_content = VALUES(comment_content), comment_time = VALUES(comment_time),
    star = VALUES(star), comment_biz_reply = VALUES(comment_biz_reply),
    recomment_content = VALUES(recomment_content), recomment_time = VALUES(recomment_time),
    user_id = VALUES(user_id), property_info = VALUES(property_info), status = VALUES(status);

INSERT INTO aishop_order.order_logistics_info
    (order_id, user_id, logistics_no, logistics_company, sender_name, sender_phone,
     sender_address, receiver_name, receiver_phone, receiver_address, logistics_status)
VALUES
    ('SM202608050002', '9000000001', NULL, NULL, 'Smarlect 华南仓', '07550000000',
     '广东省深圳市龙岗区 Smarlect 华南仓', '演示用户', '13800000001',
     '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心', 0),
    ('SM202608050003', '9000000001', 'SFDEMO202608050003', '顺丰速运', 'Smarlect 华东仓', '02100000000',
     '上海市青浦区 Smarlect 华东仓', '演示用户', '13800000001',
     '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心', 1),
    ('SM202608050004', '9000000001', 'JDDEMO202608050004', '京东物流', 'Smarlect 华南仓', '07550000000',
     '广东省深圳市龙岗区 Smarlect 华南仓', '演示用户', '13800000001',
     '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心', 2),
    ('SM202608050005', '9000000001', 'ZTDEMO202608050005', '中通快递', 'Smarlect 食品仓', '07550000001',
     '广东省东莞市 Smarlect 食品仓', '演示用户', '13800000001',
     '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心', 2),
    ('SM202608050006', '9000000001', 'SFDEMO202608050006', '顺丰速运', 'Smarlect 华东仓', '02100000000',
     '上海市青浦区 Smarlect 华东仓', '演示用户', '13800000001',
     '广东省深圳市南山区科技园科苑路 88 号 Smarlect 演示中心', 2)
ON DUPLICATE KEY UPDATE
    logistics_no = VALUES(logistics_no), logistics_company = VALUES(logistics_company),
    sender_name = VALUES(sender_name), sender_phone = VALUES(sender_phone),
    sender_address = VALUES(sender_address), receiver_name = VALUES(receiver_name),
    receiver_phone = VALUES(receiver_phone), receiver_address = VALUES(receiver_address),
    logistics_status = VALUES(logistics_status);

DELETE FROM aishop_order.order_logistics_info_record
WHERE order_id IN ('SM202608050003', 'SM202608050004', 'SM202608050005', 'SM202608050006');
INSERT INTO aishop_order.order_logistics_info_record (order_id, record_time, record_address)
VALUES
    ('SM202608050003', DATE_SUB(NOW(), INTERVAL 30 HOUR), '上海青浦转运中心已揽收'),
    ('SM202608050003', DATE_SUB(NOW(), INTERVAL 18 HOUR), '包裹已发往深圳南山营业点'),
    ('SM202608050003', DATE_SUB(NOW(), INTERVAL 4 HOUR), '深圳南山营业点派送中'),
    ('SM202608050004', DATE_SUB(NOW(), INTERVAL 4 DAY), '深圳龙岗仓已出库'),
    ('SM202608050004', DATE_SUB(NOW(), INTERVAL 3 DAY), '包裹已签收'),
    ('SM202608050005', DATE_SUB(NOW(), INTERVAL 3 DAY), '东莞食品仓已出库'),
    ('SM202608050005', DATE_SUB(NOW(), INTERVAL 2 DAY), '包裹已签收'),
    ('SM202608050006', DATE_SUB(NOW(), INTERVAL 2 DAY), '上海青浦转运中心已揽收'),
    ('SM202608050006', DATE_SUB(NOW(), INTERVAL 1 DAY), '包裹已签收');

INSERT INTO aishop_order.refund_request
    (refund_request_id, refund_order_no, source_pay_order_id, order_id, order_item_id,
     user_id, product_id, property_value_id_hash, buy_count, refund_amount, pay_channel,
     status, retry_count, next_retry_time, last_error, created_at, updated_at,
     payment_confirmed_at, completed_at)
VALUES
    ('SMREFREQ202608050007', 'SMREFUND202608050007', 'SMPAY202608050007',
     'SM202608050007', 'SMITEM202608050007', '9000000001', '088573992437392',
     '2b128a73fbcc1f31046fce5291d6d008', 1, 488.00, 'alipay', 'COMPLETED', 0,
     NULL, NULL, DATE_SUB(NOW(), INTERVAL 6 DAY), DATE_SUB(NOW(), INTERVAL 5 DAY),
     DATE_SUB(NOW(), INTERVAL 5 DAY), DATE_SUB(NOW(), INTERVAL 5 DAY))
ON DUPLICATE KEY UPDATE
    status = VALUES(status), retry_count = VALUES(retry_count),
    payment_confirmed_at = VALUES(payment_confirmed_at), completed_at = VALUES(completed_at);

INSERT INTO aishop_pay.pay_trade_record
    (trade_id, order_id, user_id, pay_order_id, channel_order_id,
     pay_channel, pay_amount, trade_status, pay_time, create_time)
VALUES
    ('SMTRADE202608050002', 'SM202608050002', '9000000001', 'SMPAY202608050002',
     'SMCHANNEL202608050002', 'alipay', 3799.00, 1, DATE_SUB(NOW(), INTERVAL 5 DAY), DATE_SUB(NOW(), INTERVAL 5 DAY)),
    ('SMTRADE202608050003', 'SM202608050003', '9000000001', 'SMPAY202608050003',
     'SMCHANNEL202608050003', 'alipay', 6379.00, 1, DATE_SUB(NOW(), INTERVAL 4 DAY), DATE_SUB(NOW(), INTERVAL 4 DAY)),
    ('SMTRADE202608050004', 'SM202608050004', '9000000001', 'SMPAY202608050004',
     'SMCHANNEL202608050004', 'alipay', 2054.00, 1, DATE_SUB(NOW(), INTERVAL 3 DAY), DATE_SUB(NOW(), INTERVAL 3 DAY)),
    ('SMTRADE202608050005', 'SM202608050005', '9000000001', 'SMPAY202608050005',
     'SMCHANNEL202608050005', 'alipay', 14.00, 1, DATE_SUB(NOW(), INTERVAL 2 DAY), DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('SMTRADE202608050006', 'SM202608050006', '9000000001', 'SMPAY202608050006',
     'SMCHANNEL202608050006', 'alipay', 1430.00, 1, DATE_SUB(NOW(), INTERVAL 1 DAY), DATE_SUB(NOW(), INTERVAL 1 DAY)),
    ('SMTRADE202608050007', 'SM202608050007', '9000000001', 'SMPAY202608050007',
     'SMCHANNEL202608050007', 'alipay', 488.00, 3, DATE_SUB(NOW(), INTERVAL 6 DAY), DATE_SUB(NOW(), INTERVAL 6 DAY))
ON DUPLICATE KEY UPDATE
    channel_order_id = VALUES(channel_order_id), pay_channel = VALUES(pay_channel),
    pay_amount = VALUES(pay_amount), trade_status = VALUES(trade_status),
    pay_time = VALUES(pay_time), create_time = VALUES(create_time);

INSERT INTO aishop_user.user_order_growth
    (order_id, user_id, pay_amount, growth_value, create_time)
VALUES
    ('SM202608050004', '9000000001', 2054.00, 2054, DATE_SUB(NOW(), INTERVAL 3 DAY)),
    ('SM202608050005', '9000000001', 14.00, 14, DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('SM202608050006', '9000000001', 1430.00, 1430, DATE_SUB(NOW(), INTERVAL 1 DAY))
ON DUPLICATE KEY UPDATE
    pay_amount = VALUES(pay_amount), growth_value = VALUES(growth_value),
    create_time = VALUES(create_time);

INSERT INTO aishop_stock.stock_change_record
    (business_key, change_type, product_id, property_value_id_hash, change_amount, created_at)
VALUES
    ('demo:order:SM202608050002', 'DEDUCT', '231335860060520', '1999572e6313cccae580033af1efda51', -1, DATE_SUB(NOW(), INTERVAL 5 DAY)),
    ('demo:order:SM202608050003', 'DEDUCT', '053997047858558', '3ef7e1f3c166b36043c55a6f224c435f', -1, DATE_SUB(NOW(), INTERVAL 4 DAY)),
    ('demo:refund:SM202608050007', 'REFUND_RESTORE', '088573992437392', '2b128a73fbcc1f31046fce5291d6d008', 1, DATE_SUB(NOW(), INTERVAL 5 DAY))
ON DUPLICATE KEY UPDATE
    change_type = VALUES(change_type), change_amount = VALUES(change_amount),
    created_at = VALUES(created_at);

-- Search examples, FAQ management data and review candidates.
INSERT INTO aishop_search.search_hot_keyword (keyword, sort, status)
VALUES
    ('降噪耳机', 100, 1), ('折叠屏手机', 90, 1), ('蓝牙音箱', 80, 1),
    ('通勤好物', 70, 1), ('办公室零食', 60, 1), ('生日礼物', 50, 1),
    ('智能家电', 40, 1), ('旅行装备', 30, 1)
ON DUPLICATE KEY UPDATE sort = VALUES(sort), status = VALUES(status);

DELETE FROM aishop_search.user_search_keyword WHERE user_id = '9000000001';
INSERT INTO aishop_search.user_search_keyword (user_id, keyword, search_time)
VALUES
    ('9000000001', '降噪耳机', NOW()),
    ('9000000001', '通勤耳机推荐', DATE_SUB(NOW(), INTERVAL 1 HOUR)),
    ('9000000001', '折叠屏手机', DATE_SUB(NOW(), INTERVAL 1 DAY)),
    ('9000000001', '蓝牙音箱', DATE_SUB(NOW(), INTERVAL 2 DAY)),
    ('9000000001', '办公室零食', DATE_SUB(NOW(), INTERVAL 3 DAY));

INSERT INTO aishop_search.rag_question
    (question_id, question, normalized_question, similar_question, answer, create_time,
     category, language, channel, priority, version, effective_start, effective_end,
     publish_status, source, owner, hit_count, update_time)
VALUES
    (9001, 'Smarlect 如何获得更准确的商品推荐？', 'smarlect如何获得更准确的商品推荐',
     '怎样描述选购需求|AI推荐需要提供什么信息',
     '请提供预算、使用场景、偏好和不能接受的条件。约束越明确，商品比较越准确。',
     NOW(), 'shopping', 'zh-CN', 'all', 100, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 26, NOW()),
    (9002, '一个订单可以使用几张优惠券？', '一个订单可以使用几张优惠券',
     '优惠券能叠加吗|多张券能否同时使用',
     '当前一个订单只能选择一张用户优惠券，提交订单时会重新校验有效期、门槛和归属。',
     NOW(), 'coupon', 'zh-CN', 'all', 95, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 18, NOW()),
    (9003, '会员等级如何计算？', '会员等级如何计算',
     '成长值多少升级银卡|金卡需要多少成长值',
     '演示规则中，1000 成长值升级银卡，5000 成长值升级金卡。',
     NOW(), 'membership', 'zh-CN', 'all', 90, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 14, NOW()),
    (9004, '订单发货后在哪里查看物流？', '订单发货后在哪里查看物流',
     '物流单号在哪里|怎么查运输轨迹',
     '进入订单详情即可查看物流公司、物流单号和运输轨迹。演示环境使用内部模拟物流。',
     NOW(), 'delivery', 'zh-CN', 'all', 85, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 21, NOW()),
    (9005, 'AI 助手会自动执行购物操作吗？', 'ai助手会自动执行购物操作吗',
     'AI会直接加入购物车吗|AI操作是否需要确认',
     '具有业务影响的动作应先展示待确认操作，只有用户确认后才执行。',
     NOW(), 'assistant', 'zh-CN', 'all', 80, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 11, NOW()),
    (9006, 'Smarlect 的对话记忆需要 Mem0 吗？', 'smarlect的对话记忆需要mem0吗',
     '记忆系统怎么实现|是否依赖外部记忆服务',
     '不需要。当前会话、摘要和购物偏好由项目自己的 MySQL 与 Redis 管理。',
     NOW(), 'assistant', 'zh-CN', 'all', 75, 1, NOW(), NULL, 'PUBLISHED',
     'Smarlect demo knowledge', 'smarlect', 8, NOW())
ON DUPLICATE KEY UPDATE
    question = VALUES(question), normalized_question = VALUES(normalized_question),
    similar_question = VALUES(similar_question), answer = VALUES(answer),
    category = VALUES(category), priority = VALUES(priority),
    publish_status = VALUES(publish_status), source = VALUES(source), owner = VALUES(owner),
    hit_count = VALUES(hit_count), update_time = VALUES(update_time);

INSERT INTO aishop_search.faq_candidate
    (question, normalized_hash, answer, category, source_message_id, frequency,
     status, reviewer, review_remark, created_at, updated_at)
VALUES
    ('能否按预算自动比较两款商品？', SHA2('能否按预算自动比较两款商品', 256),
     '可以，建议同时提供预算、使用场景和最在意的三项指标。', 'shopping', NULL, 7,
     'PENDING', NULL, NULL, NOW(), NOW()),
    ('人工客服接入后还能返回 AI 吗？', SHA2('人工客服接入后还能返回AI吗', 256),
     '可以，运营人员处理完成后可以将会话交还 AI。', 'support', NULL, 4,
     'PENDING', NULL, NULL, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    answer = VALUES(answer), category = VALUES(category), frequency = VALUES(frequency),
    status = VALUES(status), updated_at = VALUES(updated_at);

-- Stable seven-day dashboard series.
INSERT INTO aishop_admin.statistics_info (statistics_date, data_type, data_value)
VALUES
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 7 DAY), '%Y-%m-%d'), 1, 8420.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 7 DAY), '%Y-%m-%d'), 2, 18),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 7 DAY), '%Y-%m-%d'), 3, 210.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 7 DAY), '%Y-%m-%d'), 4, 1),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 DAY), '%Y-%m-%d'), 1, 11360.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 DAY), '%Y-%m-%d'), 2, 24),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 DAY), '%Y-%m-%d'), 3, 488.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 DAY), '%Y-%m-%d'), 4, 1),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 5 DAY), '%Y-%m-%d'), 1, 13680.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 5 DAY), '%Y-%m-%d'), 2, 2),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 5 DAY), '%Y-%m-%d'), 3, 0.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 5 DAY), '%Y-%m-%d'), 4, 0),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 4 DAY), '%Y-%m-%d'), 1, 16240.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 4 DAY), '%Y-%m-%d'), 2, 31),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 4 DAY), '%Y-%m-%d'), 3, 320.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 4 DAY), '%Y-%m-%d'), 4, 2),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), '%Y-%m-%d'), 1, 20540.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), '%Y-%m-%d'), 2, 35),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), '%Y-%m-%d'), 3, 0.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), '%Y-%m-%d'), 4, 0),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), '%Y-%m-%d'), 1, 14890.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), '%Y-%m-%d'), 2, 28),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), '%Y-%m-%d'), 3, 126.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), '%Y-%m-%d'), 4, 1),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%Y-%m-%d'), 1, 23180.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%Y-%m-%d'), 2, 42),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%Y-%m-%d'), 3, 260.00),
    (DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%Y-%m-%d'), 4, 1)
ON DUPLICATE KEY UPDATE data_value = VALUES(data_value);

DELETE FROM aishop_admin.admin_audit_log WHERE action = 'SMARLECT_DEMO_SEED';
INSERT INTO aishop_admin.admin_audit_log
    (operator, action, target_user_id, detail, create_time)
VALUES
    ('smarlect', 'SMARLECT_DEMO_SEED', '9000000001',
     '{"scope":"local-demo","payment":false,"email":false,"imageModeration":false,"amap":false}',
     NOW());

COMMIT;
