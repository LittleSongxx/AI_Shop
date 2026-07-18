package com.myshop.entity.enums;

import java.util.Arrays;
import java.util.Optional;

public enum RushingCouponStatusEnum {
    NO(0, "否"),
    YES(1, "是");

    private Integer status;
    private String desc;

    RushingCouponStatusEnum(Integer status, String desc) {
        this.status = status;
        this.desc = desc;
    }

    public Integer getStatus() {
        return status;
    }

    public String getDesc() {
        return desc;
    }


    public static CommentStatusEnum getByStatus(Integer status) {
        Optional<CommentStatusEnum> typeEnum = Arrays.stream(CommentStatusEnum.values()).filter(value -> value.getStatus().equals(status)).findFirst();
        return typeEnum == null ? null : typeEnum.get();
    }
}
