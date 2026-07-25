package com.aishop.controller.admin;

import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PostMapping;

import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminControllerSurfaceTest {

    @Test
    void generatedOrderWriteRoutesStayDisabled() {
        assertRoutesAbsent(OrderInfoController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateOrderInfoByOrderId", "/deleteOrderInfoByOrderId");
        assertRoutesAbsent(OrderItemController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateOrderItemByOrderItemId", "/deleteOrderItemByOrderItemId");
        assertRoutesAbsent(OrderLogisticsInfoController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateOrderLogisticsInfoByOrderId", "/deleteOrderLogisticsInfoByOrderId");
        assertRoutesAbsent(OrderLogisticsInfoRecordController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateOrderLogisticsInfoRecordByRecordId",
                "/deleteOrderLogisticsInfoRecordByRecordId");
        assertRoutesAbsent(OrderCommentController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateOrderCommentByOrderId", "/deleteOrderCommentByOrderId");
    }

    private static void assertRoutesAbsent(Class<?> controller, String... forbidden) {
        Set<String> routes = Arrays.stream(controller.getDeclaredMethods())
                .map(method -> method.getAnnotation(PostMapping.class))
                .filter(java.util.Objects::nonNull)
                .flatMap(mapping -> Arrays.stream(mapping.value()))
                .collect(Collectors.toSet());
        assertTrue(routes.stream().noneMatch(Set.of(forbidden)::contains),
                () -> controller.getSimpleName() + " exposes a forbidden route: " + routes);
    }
}
