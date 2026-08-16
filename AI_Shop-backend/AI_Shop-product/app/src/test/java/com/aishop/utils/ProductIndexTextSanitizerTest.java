package com.aishop.utils;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ProductIndexTextSanitizerTest {

    @Test
    void removesImageOnlyDescriptions() {
        String source = """
                ![](/api/file/getResource?sourceName=first.png)
                ![](/api/file/getResource?sourceName=second.png)
                """;

        assertEquals("", ProductIndexTextSanitizer.sanitize(source));
    }

    @Test
    void preservesUsefulAltLinkAndHtmlText() {
        String source = """
                ![轻薄机身](/images/laptop.png)
                适合 <strong>移动办公</strong>，查看[保修政策](/warranty)。
                """;

        assertEquals(
                "轻薄机身 适合 移动办公 ，查看保修政策。",
                ProductIndexTextSanitizer.sanitize(source));
    }
}
