USE simlect_stock;
CREATE TABLE IF NOT EXISTS sku_stock (
    product_id             varchar(15)  NOT NULL COMMENT '商品ID',
    property_value_id_hash varchar(32)  NOT NULL COMMENT '属性值id组hash',
    stock                  int          NOT NULL COMMENT '库存',
    PRIMARY KEY (product_id, property_value_id_hash)
) COMMENT 'SKU库存（从 product_sku.stock 迁出）' COLLATE = utf8mb4_general_ci;
