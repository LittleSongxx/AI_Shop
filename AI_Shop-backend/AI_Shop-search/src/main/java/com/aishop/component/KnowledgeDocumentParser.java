package com.aishop.component;

import com.aishop.exception.BusinessException;
import org.apache.tika.exception.EncryptedDocumentException;
import org.apache.tika.metadata.Metadata;
import org.apache.tika.parser.AutoDetectParser;
import org.apache.tika.parser.ParseContext;
import org.apache.tika.sax.BodyContentHandler;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

@Component
public class KnowledgeDocumentParser {

    public static final long MAX_FILE_SIZE = 10L * 1024 * 1024;
    private static final int MAX_CHUNK_CHARS = 1200;
    private static final int CHUNK_OVERLAP_CHARS = 120;
    private static final Set<String> ALLOWED_TYPES = Set.of("txt", "md", "pdf", "docx");
    private static final Pattern MARKDOWN_HEADING = Pattern.compile("^#{1,6}\\s+.+$");

    public ParsedDocument parse(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException("知识文档不能为空");
        }
        if (file.getSize() > MAX_FILE_SIZE) {
            throw new BusinessException("单个知识文档不能超过10MB");
        }
        String sourceName = file.getOriginalFilename() == null
                ? "knowledge.txt" : file.getOriginalFilename().trim();
        String fileType = extension(sourceName);
        if (!ALLOWED_TYPES.contains(fileType)) {
            throw new BusinessException("仅支持TXT、Markdown、PDF和DOCX文档");
        }

        try (InputStream inputStream = file.getInputStream()) {
            AutoDetectParser parser = new AutoDetectParser();
            BodyContentHandler handler = new BodyContentHandler(-1);
            Metadata metadata = new Metadata();
            parser.parse(inputStream, handler, metadata, new ParseContext());
            String normalized = normalize(handler.toString());
            if (normalized.isBlank()) {
                throw new BusinessException("文档未解析出可用文本，不支持扫描件或空文档");
            }
            return new ParsedDocument(
                    sourceName,
                    fileType,
                    normalized,
                    metadata.get(Metadata.CONTENT_TYPE),
                    chunk(normalized)
            );
        } catch (EncryptedDocumentException e) {
            throw new BusinessException("不支持加密文档，请解除密码后重新上传", e);
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            throw new BusinessException("文档解析失败：" + e.getMessage(), e);
        }
    }

    public List<Chunk> chunk(String normalizedText) {
        List<Section> sections = sections(normalizedText);
        List<Chunk> chunks = new ArrayList<>();
        int index = 0;
        for (Section section : sections) {
            for (String content : splitSection(section.content())) {
                String text = section.heading().isBlank()
                        ? content : section.heading() + "\n" + content;
                chunks.add(new Chunk(index++, section.heading(), text, estimateTokens(text)));
            }
        }
        return chunks;
    }

    String normalize(String value) {
        String[] lines = (value == null ? "" : value)
                .replace("\u0000", "")
                .replace("\r\n", "\n")
                .replace('\r', '\n')
                .split("\n", -1);
        StringBuilder result = new StringBuilder();
        int blankCount = 0;
        for (String line : lines) {
            String normalizedLine = line
                    .replace('\u00A0', ' ')
                    .replaceAll("[\\t ]+", " ")
                    .trim();
            if (normalizedLine.isBlank()) {
                blankCount++;
                if (blankCount <= 1 && !result.isEmpty()) {
                    if (result.charAt(result.length() - 1) != '\n') {
                        result.append("\n\n");
                    } else if (result.length() < 2 || result.charAt(result.length() - 2) != '\n') {
                        result.append('\n');
                    }
                }
                continue;
            }
            blankCount = 0;
            if (!result.isEmpty() && result.charAt(result.length() - 1) != '\n') {
                result.append('\n');
            }
            result.append(normalizedLine);
        }
        return result.toString().trim();
    }

    private List<Section> sections(String text) {
        List<Section> sections = new ArrayList<>();
        String heading = "";
        StringBuilder content = new StringBuilder();
        for (String line : text.split("\n")) {
            if (isHeading(line)) {
                addSection(sections, heading, content);
                heading = line.replaceFirst("^#{1,6}\\s+", "").trim();
                content.setLength(0);
                continue;
            }
            if (!content.isEmpty()) {
                content.append('\n');
            }
            content.append(line);
        }
        addSection(sections, heading, content);
        if (sections.isEmpty() && !text.isBlank()) {
            sections.add(new Section("", text));
        }
        return sections;
    }

    private boolean isHeading(String line) {
        String value = line == null ? "" : line.trim();
        return MARKDOWN_HEADING.matcher(value).matches()
                || (value.length() <= 60 && (value.endsWith("：") || value.endsWith(":")));
    }

    private void addSection(List<Section> sections, String heading, StringBuilder content) {
        String value = content.toString().trim();
        if (!value.isBlank()) {
            sections.add(new Section(heading, value));
        }
    }

    private List<String> splitSection(String value) {
        List<String> chunks = new ArrayList<>();
        String remaining = value.trim();
        while (remaining.length() > MAX_CHUNK_CHARS) {
            int split = bestSplit(remaining, MAX_CHUNK_CHARS);
            String chunk = remaining.substring(0, split).trim();
            if (!chunk.isBlank()) {
                chunks.add(chunk);
            }
            int next = Math.max(0, split - CHUNK_OVERLAP_CHARS);
            remaining = remaining.substring(next).trim();
        }
        if (!remaining.isBlank()) {
            chunks.add(remaining);
        }
        return chunks;
    }

    private int bestSplit(String value, int target) {
        int minimum = Math.max(1, target - 240);
        for (int i = Math.min(target, value.length() - 1); i >= minimum; i--) {
            char current = value.charAt(i);
            if (current == '\n' || current == '。' || current == '！'
                    || current == '？' || current == ';' || current == '；') {
                return i + 1;
            }
        }
        return Math.min(target, value.length());
    }

    private int estimateTokens(String value) {
        int ascii = 0;
        for (int i = 0; i < value.length(); i++) {
            if (value.charAt(i) < 128) {
                ascii++;
            }
        }
        int nonAscii = value.length() - ascii;
        return Math.max(1, nonAscii + (ascii + 3) / 4);
    }

    private String extension(String name) {
        int index = name.lastIndexOf('.');
        if (index < 0 || index == name.length() - 1) {
            return "";
        }
        return name.substring(index + 1).toLowerCase(Locale.ROOT);
    }

    private record Section(String heading, String content) {
    }

    public record Chunk(int index, String heading, String content, int tokenCount) {
    }

    public record ParsedDocument(
            String sourceName,
            String fileType,
            String normalizedText,
            String mediaType,
            List<Chunk> chunks
    ) {
    }
}
