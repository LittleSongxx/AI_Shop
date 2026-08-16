package com.aishop.entity.enums;


import java.util.Arrays;

public enum RagDataTypeEnum {
    PRODUCT("product", "商品数据"),
    FAQ("faq", "FAQ数据");

    private String type;

    private String desc;

    RagDataTypeEnum(String type, String desc) {
        this.type = type;
        this.desc = desc;
    }

    public String getType() {
        return type;
    }

    public String getDesc() {
        return desc;
    }

    public static RagDataTypeEnum getByType(String type) {
        return Arrays.stream(RagDataTypeEnum.values())
                .filter(value -> value.getType().equals(type))
                .findFirst()
                .orElse(null);
    }
}
