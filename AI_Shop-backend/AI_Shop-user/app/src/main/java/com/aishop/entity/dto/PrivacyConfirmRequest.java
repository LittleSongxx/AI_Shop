package com.aishop.entity.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record PrivacyConfirmRequest(
        @NotBlank(message = "password 不能为空")
        @Size(max = 128, message = "password 长度不能超过 128")
        String password) {
}
