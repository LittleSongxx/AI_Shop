package com.aishop.controller.admin;

import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PostMapping;

import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminControllerSurfaceTest {

    @Test
    void generatedCartWriteRoutesStayDisabled() {
        Set<String> routes = Arrays.stream(ProductCartController.class.getDeclaredMethods())
                .map(method -> method.getAnnotation(PostMapping.class))
                .filter(java.util.Objects::nonNull)
                .flatMap(mapping -> Arrays.stream(mapping.value()))
                .collect(Collectors.toSet());

        Set<String> forbidden = Set.of(
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateProductCartByCartId", "/deleteProductCartByCartId",
                "/deleteProductCartByProductIdAndPropertyValueIdHashAndUserId");
        assertTrue(routes.stream().noneMatch(forbidden::contains),
                () -> "ProductCartController exposes a forbidden route: " + routes);
    }
}
