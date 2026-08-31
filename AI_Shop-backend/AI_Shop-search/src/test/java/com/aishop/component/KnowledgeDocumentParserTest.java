package com.aishop.component;

import com.aishop.exception.BusinessException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.font.PDType1Font;
import org.apache.pdfbox.pdmodel.font.Standard14Fonts;
import org.apache.pdfbox.pdmodel.graphics.image.LosslessFactory;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;

import java.awt.Color;
import java.awt.Font;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KnowledgeDocumentParserTest {

    private final KnowledgeDocumentParser parser = new KnowledgeDocumentParser();

    @BeforeEach
    void disableImageDescription() {
        ReflectionTestUtils.setField(
                parser, "imageVlmDescriber", new ImageVlmDescriber(new ObjectMapper()));
    }

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

    @Test
    void parsesTextFromRealPdf() throws IOException {
        MockMultipartFile file = new MockMultipartFile(
                "file", "policy.pdf", "application/pdf", textPdf("Refund policy applies."));

        KnowledgeDocumentParser.ParsedDocument parsed = parser.parse(file);

        assertEquals("pdf", parsed.fileType());
        assertTrue(parsed.normalizedText().contains("Refund policy applies."));
        assertTrue(parsed.chunks().stream()
                .anyMatch(chunk -> chunk.content().contains("Refund policy applies.")));
    }

    @Test
    void parsesTextFromRealDocx() throws IOException {
        MockMultipartFile file = new MockMultipartFile(
                "file", "policy.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                textDocx("Return requests start from order details."));

        KnowledgeDocumentParser.ParsedDocument parsed = parser.parse(file);

        assertEquals("docx", parsed.fileType());
        assertTrue(parsed.normalizedText().contains("Return requests start from order details."));
    }

    @Test
    void rejectsImageOnlyScannedPdfWithoutOcr() throws IOException {
        MockMultipartFile file = new MockMultipartFile(
                "file", "scan.pdf", "application/pdf", imageOnlyPdf());

        BusinessException error = assertThrows(
                BusinessException.class, () -> parser.parse(file));

        assertTrue(error.getMessage().contains("不支持扫描件或空文档"));
    }

    private static byte[] textPdf(String text) throws IOException {
        try (PDDocument document = new PDDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            PDPage page = new PDPage();
            document.addPage(page);
            try (PDPageContentStream content = new PDPageContentStream(document, page)) {
                content.beginText();
                content.setFont(
                        new PDType1Font(Standard14Fonts.FontName.HELVETICA), 12);
                content.newLineAtOffset(72, 720);
                content.showText(text);
                content.endText();
            }
            document.save(output);
            return output.toByteArray();
        }
    }

    private static byte[] textDocx(String text) throws IOException {
        try (XWPFDocument document = new XWPFDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            document.createParagraph().createRun().setText(text);
            document.write(output);
            return output.toByteArray();
        }
    }

    private static byte[] imageOnlyPdf() throws IOException {
        try (PDDocument document = new PDDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            PDPage page = new PDPage();
            document.addPage(page);
            BufferedImage scan = new BufferedImage(640, 160, BufferedImage.TYPE_INT_RGB);
            var graphics = scan.createGraphics();
            graphics.setColor(Color.WHITE);
            graphics.fillRect(0, 0, scan.getWidth(), scan.getHeight());
            graphics.setColor(Color.BLACK);
            graphics.setFont(new Font(Font.SANS_SERIF, Font.PLAIN, 28));
            graphics.drawString("SCANNED REFUND POLICY", 40, 90);
            graphics.dispose();
            try (PDPageContentStream content = new PDPageContentStream(document, page)) {
                content.drawImage(LosslessFactory.createFromImage(document, scan), 72, 680);
            }
            document.save(output);
            return output.toByteArray();
        }
    }
}
