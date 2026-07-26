-- ============================================================
-- AI_Shop 索引优化回滚脚本
-- 对应：index_optimization.sql
-- 作用：删除优化脚本新增的索引，恢复优化前的索引状态
--
-- 【分库】一域一库，涉及 5 个库，每段前面各有自己的 USE，按段执行。
-- 库名与归属见 sql/TABLE_OWNERSHIP.md。
-- ============================================================

-- order_info：删除 P0 新增索引，恢复被删的冗余索引
USE aishop_order;

ALTER TABLE order_info
    DROP INDEX idx_order_status_time;

ALTER TABLE order_info
    ADD INDEX idx_pay_order_id (pay_order_id);

-- comment_report
ALTER TABLE comment_report
    DROP INDEX idx_status_report_time;

-- product_info：删除 P1 新增索引（保留原 idx_product_commend / idx_product_category_status）
USE aishop_product;

ALTER TABLE product_info
    DROP INDEX idx_commend_status_time;

ALTER TABLE product_info
    DROP INDEX idx_status_commend_sale;

ALTER TABLE product_info
    DROP INDEX idx_status_pcategory_sale;

ALTER TABLE product_info
    DROP INDEX idx_status_category_sale;

ALTER TABLE product_info
    DROP INDEX idx_status_min_price_sale;

-- image_moderation_record（仅删联合索引，保留原 idx_create_time）
USE aishop_user;

ALTER TABLE image_moderation_record
    DROP INDEX idx_status_create_time;

-- ------------------------------------------------------------
-- mq_compensation_log：四个库各有一份，四段都要执行
-- ------------------------------------------------------------
USE aishop_order;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_status_create_time;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_scene_status_time;

USE aishop_product;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_status_create_time;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_scene_status_time;

USE aishop_search;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_status_create_time;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_scene_status_time;

USE aishop_admin;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_status_create_time;
ALTER TABLE mq_compensation_log
    DROP INDEX idx_scene_status_time;
