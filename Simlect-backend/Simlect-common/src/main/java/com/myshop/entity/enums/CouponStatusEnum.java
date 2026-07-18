package com.myshop.entity.enums;

import java.util.Arrays;
import java.util.Optional;

public enum CouponStatusEnum {

    STOP(0, "已停用"),
    NORMAL(1, "正常"),
    OVERDUE(2, "已过期"),
    FINISH(3, "已发完");

    private Integer status;
    private String desc;

    CouponStatusEnum(Integer status, String desc) {
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
