package com.aishop.api.enums;

public enum ImageModerationSceneEnum {
    AVATAR("avatar", "头像"),
    COMMENT("comment", "评论图片"),
    AGENT("agent", "AI 客服图片");

    private final String code;
    private final String desc;

    ImageModerationSceneEnum(String code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    public String getCode() {
        return code;
    }

    public String getDesc() {
        return desc;
    }

    public static ImageModerationSceneEnum getByCode(String code) {
        if (code == null || code.isBlank()) {
            return null;
        }
        for (ImageModerationSceneEnum value : values()) {
            if (value.code.equalsIgnoreCase(code)) {
                return value;
            }
        }
        return null;
    }
}
