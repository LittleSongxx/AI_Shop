package com.aishop.entity.enums;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class RagDataTypeEnumTest {

    @Test
    void resolvesKnownTypes() {
        assertEquals(RagDataTypeEnum.PRODUCT, RagDataTypeEnum.getByType("product"));
        assertEquals(RagDataTypeEnum.FAQ, RagDataTypeEnum.getByType("faq"));
    }

    @Test
    void returnsNullForUnknownOrMissingType() {
        assertNull(RagDataTypeEnum.getByType("unknown"));
        assertNull(RagDataTypeEnum.getByType(null));
    }
}
