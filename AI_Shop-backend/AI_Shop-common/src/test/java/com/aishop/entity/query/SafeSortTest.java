package com.aishop.entity.query;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class SafeSortTest {

    @Test
    void acceptsIdentifiersAliasesAndDirections() {
        assertEquals(
                "p.create_time desc, total_sale asc",
                SafeSort.of("p.create_time DESC, total_sale ASC").toString());
    }

    @Test
    void rejectsSqlExpressionsAndComments() {
        assertThrows(IllegalArgumentException.class,
                () -> SafeSort.of("create_time desc; drop table product_info"));
        assertThrows(IllegalArgumentException.class,
                () -> SafeSort.of("coalesce(update_time, create_time) desc"));
        assertThrows(IllegalArgumentException.class,
                () -> SafeSort.of("create_time desc --"));
        assertThrows(IllegalArgumentException.class,
                () -> SafeSort.of("(select sleep(1))"));
    }

    @Test
    void removesOnlyRequestedQualifier() {
        assertEquals("create_time desc", SafeSort.of("p.create_time desc").withoutQualifier("p").toString());
    }
}
