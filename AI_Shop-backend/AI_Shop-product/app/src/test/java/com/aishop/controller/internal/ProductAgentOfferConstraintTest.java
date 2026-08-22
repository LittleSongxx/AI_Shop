package com.aishop.controller.internal;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ProductAgentOfferConstraintTest {

    @Test
    void parsesOnlyNonEmptySkuAllowLists() {
        Map<String, Set<String>> parsed = ProductAgentInternalController.parseAllowedSkuKeys(
                Map.of(
                        "p1", List.of("sku-a", "sku-b", "sku-a"),
                        "p2", List.of("", " ")));

        assertEquals(Map.of("p1", Set.of("sku-a", "sku-b")), parsed);
    }
}
