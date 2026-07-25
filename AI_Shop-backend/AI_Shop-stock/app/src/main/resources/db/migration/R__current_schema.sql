-- Current schema owned by the stock service.
CREATE TABLE IF NOT EXISTS sku_stock (
    product_id             varchar(15)  NOT NULL COMMENT '商品ID',
    property_value_id_hash varchar(32)  NOT NULL COMMENT '属性值id组hash',
    stock                  int          NOT NULL COMMENT '库存',
    PRIMARY KEY (product_id, property_value_id_hash)
) COMMENT 'SKU库存（从 product_sku.stock 迁出）' COLLATE = utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS stock_change_record (
    business_key            varchar(96)                         NOT NULL PRIMARY KEY,
    change_type             varchar(32)                         NOT NULL,
    product_id              varchar(15)                         NOT NULL,
    property_value_id_hash  varchar(32)                         NOT NULL,
    change_amount           int                                 NOT NULL,
    created_at              datetime default current_timestamp  NOT NULL,
    KEY idx_stock_change_sku (product_id, property_value_id_hash)
) COMMENT '库存幂等变更记录' CHARSET = utf8mb4;
