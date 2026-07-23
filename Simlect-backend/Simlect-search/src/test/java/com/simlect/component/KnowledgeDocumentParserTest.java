package com.simlect.component;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KnowledgeDocumentParserTest {

    private final KnowledgeDocumentParser parser = new KnowledgeDocumentParser();

    @Test
    void keepsMarkdownHeadingAndSplitsLongPolicy() {
        String text = "# 退款政策\n" + "退款说明。".repeat(350) + "\n## 运费\n以订单页面为准。";

        List<KnowledgeDocumentParser.Chunk> chunks = parser.chunk(parser.normalize(text));

        assertTrue(chunks.size() >= 3);
        assertEquals("退款政策", chunks.get(0).heading());
        assertTrue(chunks.get(0).content().startsWith("退款政策\n"));
        assertEquals("运费", chunks.get(chunks.size() - 1).heading());
    }

    @Test
    void normalizesWhitespaceWithoutFlatteningParagraphs() {
        String normalized = parser.normalize("第一段  \r\n\r\n\r\n 第二段\t内容 ");

        assertEquals("第一段\n\n第二段 内容", normalized);
    }
}
