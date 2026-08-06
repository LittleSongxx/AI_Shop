-- ============================================================
-- AI_Shop 索引优化脚本
--
-- 说明：请在业务低峰执行；大表 ADD INDEX 可能锁表，生产环境建议 pt-osc/gh-ost
-- 执行后：运行文末 EXPLAIN 验证
--
-- 【分库】本项目是「一域一库」，涉及的表分散在 5 个库里，不能用一个 USE 跑完整个脚本。
-- 每段前面都有各自的 USE，按段执行；库名与归属见 sql/分库表归属.md。
--   aishop_order   : order_info, comment_report, order_logistics_info
--   aishop_product : product_info
--   aishop_user    : image_moderation_record（写入侧，admin 经 Feign 访问）
--   mq_compensation_log 在 order/product/search/admin 四个库各有一份，需分别执行
-- ============================================================

-- ============================================================
-- P0 订单超时关单 / 待付款扫描
-- 对应：order_status = 0 AND order_time <= ?
--       OrderInfoServiceImpl 支付超时关单、MQ 延迟关单
-- ============================================================
USE aishop_order;

ALTER TABLE order_info
    ADD INDEX idx_order_status_time (order_status, order_time);

-- 冗余：pay_order_id 两个索引完全重复，删除其一（保留 idx_order_pay_order）
ALTER TABLE order_info
    DROP INDEX idx_pay_order_id;

-- idx_create_time 仅 order_time，无法覆盖「待付款+超时」；保留作其他按时间统计即可

-- ============================================================
-- P1 首页推荐 /product/loadCommendProduct
-- 对应：commend_type=1 AND status=1 ORDER BY create_time DESC LIMIT 11
-- 注意：应用层需 query.setStatus(1)，见 ProductController.loadCommendProduct
-- ============================================================
USE aishop_product;

ALTER TABLE product_info
    ADD INDEX idx_commend_status_time (commend_type, status, create_time DESC);

-- 新索引覆盖旧 idx_product_commend 后可删除（可选，减写入开销）
-- ALTER TABLE product_info DROP INDEX idx_product_commend;

-- ============================================================
-- P1 商品列表 /product/loadProduct
-- 首页：status=1, commend_type=0, ORDER BY total_sale / min_price
-- 分类：status=1 AND (category_id=? OR p_category_id=?)
-- ============================================================
ALTER TABLE product_info
    ADD INDEX idx_status_commend_sale (status, commend_type, total_sale DESC);

ALTER TABLE product_info
    ADD INDEX idx_status_pcategory_sale (status, p_category_id, total_sale DESC);

ALTER TABLE product_info
    ADD INDEX idx_status_category_sale (status, category_id, total_sale DESC);

-- 按价格排序场景（sortKey=PRICE）
ALTER TABLE product_info
    ADD INDEX idx_status_min_price_sale (status, min_price, total_sale DESC);

-- ============================================================
-- P2 管理端：状态筛选 + 时间倒序（消除 Using filesort）
-- ============================================================

-- 评论举报（aishop_order）
USE aishop_order;

ALTER TABLE comment_report
    ADD INDEX idx_status_report_time (status, report_time DESC);

-- 图片审核（aishop_user 为写入侧，以此库为准）
USE aishop_user;

ALTER TABLE image_moderation_record
    ADD INDEX idx_status_create_time (status, create_time DESC);

-- ------------------------------------------------------------
-- MQ 补偿审查 MqCompensationLogList
-- mq_compensation_log 在四个库各有一份独立的表，四段都要执行。
-- ------------------------------------------------------------
USE aishop_order;
ALTER TABLE mq_compensation_log
    ADD INDEX idx_status_create_time (status, create_time DESC);
ALTER TABLE mq_compensation_log
    ADD INDEX idx_scene_status_time (biz_scene, status, create_time DESC);

USE aishop_product;
ALTER TABLE mq_compensation_log
    ADD INDEX idx_status_create_time (status, create_time DESC);
ALTER TABLE mq_compensation_log
    ADD INDEX idx_scene_status_time (biz_scene, status, create_time DESC);

USE aishop_search;
ALTER TABLE mq_compensation_log
    ADD INDEX idx_status_create_time (status, create_time DESC);
ALTER TABLE mq_compensation_log
    ADD INDEX idx_scene_status_time (biz_scene, status, create_time DESC);

USE aishop_admin;
ALTER TABLE mq_compensation_log
    ADD INDEX idx_status_create_time (status, create_time DESC);
ALTER TABLE mq_compensation_log
    ADD INDEX idx_scene_status_time (biz_scene, status, create_time DESC);

-- ============================================================
-- P3 冗余索引清理（可选，确认无单独使用单列索引的慢查后再删）
-- ============================================================

-- agent_message（aishop_agent）：idx_user_id 已被 idx_agent_user_time(user_id, send_time) 左前缀覆盖
-- USE aishop_agent;
-- ALTER TABLE agent_message DROP INDEX idx_user_id;

-- order_logistics_info（aishop_order）：tracking_no 与 idx_tracking_no 重复
-- USE aishop_order;
-- ALTER TABLE order_logistics_info DROP INDEX tracking_no;

-- mq_compensation_log：加联合索引后，单列 idx_status 可酌情删除（四个库各一份）
-- ALTER TABLE mq_compensation_log DROP INDEX idx_status;

-- ============================================================
-- 验证 EXPLAIN（执行后手动跑，期望 type=ref/range，filesort 减少或消失）
-- 注意每段前的 USE，跨库不能连着跑。
-- ============================================================
/*
USE aishop_product;
EXPLAIN SELECT product_id FROM product_info
WHERE commend_type = 1 AND status = 1
ORDER BY create_time DESC LIMIT 11;

EXPLAIN SELECT product_id FROM product_info
WHERE status = 1 AND p_category_id = '01001'
ORDER BY total_sale DESC LIMIT 15;

USE aishop_order;
EXPLAIN SELECT order_id FROM order_info
WHERE order_status = 0 AND order_time <= NOW()
LIMIT 100;

EXPLAIN SELECT log_id FROM mq_compensation_log
WHERE status = 0
ORDER BY create_time DESC LIMIT 15;
*/
