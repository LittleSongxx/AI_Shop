package com.aishop.controller.admin;

import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PostMapping;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminControllerSurfaceTest {

    @Test
    void generatedProductWriteRoutesStayDisabled() {
        assertRoutesAbsent(ProductInfoController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateProductInfoByProductId", "/deleteProductInfoByProductId");
        assertRoutesAbsent(ProductSkuController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateProductSkuByProductIdAndPropertyValueIdHash",
                "/deleteProductSkuByProductIdAndPropertyValueIdHash");
        assertRoutesAbsent(ProductPropertyValueController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateProductPropertyValueByProductIdAndPropertyValueId",
                "/deleteProductPropertyValueByProductIdAndPropertyValueId");
        assertRoutesAbsent(SysCategoryController.class,
                "/addBatch", "/addOrUpdateBatch", "/updateSysCategoryByCategoryId");
        assertRoutesAbsent(SysProductPropertyController.class,
                "/add", "/addBatch", "/addOrUpdateBatch",
                "/updateSysProductPropertyByPropertyId", "/deleteSysProductPropertyByPropertyId");
    }

    private static void assertRoutesAbsent(Class<?> controller, String... forbidden) {
        Set<String> routes = Arrays.stream(controller.getDeclaredMethods())
                .map(Method::getDeclaredAnnotations)
                .flatMap(Arrays::stream)
                .filter(PostMapping.class::isInstance)
                .map(PostMapping.class::cast)
                .flatMap(mapping -> Arrays.stream(mapping.value()))
                .collect(Collectors.toSet());
        assertTrue(routes.stream().noneMatch(Set.of(forbidden)::contains),
                () -> controller.getSimpleName() + " exposes a forbidden route: " + routes);
    }
}
