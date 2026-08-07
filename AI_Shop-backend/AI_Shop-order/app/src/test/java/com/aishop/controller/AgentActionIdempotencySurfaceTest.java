package com.aishop.controller;

import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.RequestHeader;

import java.lang.reflect.Method;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class AgentActionIdempotencySurfaceTest {

    @Test
    void allAgentWriteRoutesBindIdempotencyKey() throws Exception {
        assertIdempotencyHeader(
                OrderController.class.getDeclaredMethod(
                        "cancelOrder", String.class, String.class));
        assertIdempotencyHeader(
                OrderController.class.getDeclaredMethod(
                        "confirmOrder", String.class, String.class));
        assertIdempotencyHeader(
                OrderController.class.getDeclaredMethod(
                        "refundOrder", String.class, String.class));
        assertIdempotencyHeader(
                OrderCommentController.class.getDeclaredMethod(
                        "postComment",
                        String.class,
                        String.class,
                        String.class,
                        Integer.class,
                        String.class));
        assertIdempotencyHeader(
                OrderCommentController.class.getDeclaredMethod(
                        "postReComment",
                        String.class,
                        String.class,
                        String.class,
                        String.class));
    }

    private static void assertIdempotencyHeader(Method method) {
        RequestHeader header = method.getParameters()[method.getParameterCount() - 1]
                .getAnnotation(RequestHeader.class);
        assertNotNull(header, method + " must bind Idempotency-Key");
        assertEquals("Idempotency-Key", header.value());
    }
}
