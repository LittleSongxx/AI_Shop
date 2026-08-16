package com.aishop.utils;

import java.util.regex.Pattern;

public final class ProductIndexTextSanitizer {

    private static final Pattern MARKDOWN_IMAGE =
            Pattern.compile("!\\[([^\\]]*)]\\([^\\r\\n]*\\)");
    private static final Pattern MARKDOWN_LINK =
            Pattern.compile("(?<!!)\\[([^\\]]+)]\\([^\\r\\n]*\\)");
    private static final Pattern HTML_IMAGE =
            Pattern.compile("<img\\b[^>]*>", Pattern.CASE_INSENSITIVE);
    private static final Pattern HTML_TAG =
            Pattern.compile("<[^>]+>");
    private static final Pattern BARE_URL =
            Pattern.compile("https?://\\S+", Pattern.CASE_INSENSITIVE);
    private static final Pattern WHITESPACE =
            Pattern.compile("\\s+");

    private ProductIndexTextSanitizer() {
    }

    public static String sanitize(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        String text = value
                .replace('\u0000', ' ')
                .replace('\u00A0', ' ');
        text = MARKDOWN_IMAGE.matcher(text).replaceAll("$1");
        text = MARKDOWN_LINK.matcher(text).replaceAll("$1");
        text = HTML_IMAGE.matcher(text).replaceAll(" ");
        text = HTML_TAG.matcher(text).replaceAll(" ");
        text = BARE_URL.matcher(text).replaceAll(" ");
        text = text
                .replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&#39;", "'");
        return WHITESPACE.matcher(text).replaceAll(" ").trim();
    }
}
