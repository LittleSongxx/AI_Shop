package com.aishop.entity.query;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * SQL sort fragment built only from identifiers and ASC/DESC directions.
 */
public final class SafeSort {

    private static final Pattern TERM_PATTERN = Pattern.compile(
            "^([A-Za-z_][A-Za-z0-9_]*\\.)?([A-Za-z_][A-Za-z0-9_]*)(?:\\s+(asc|desc))?$",
            Pattern.CASE_INSENSITIVE);

    private final String sql;

    private SafeSort(String sql) {
        this.sql = sql;
    }

    public static SafeSort of(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String[] rawTerms = value.split(",", -1);
        List<String> terms = new ArrayList<>(rawTerms.length);
        for (String rawTerm : rawTerms) {
            String term = rawTerm.trim().replaceAll("\\s+", " ");
            Matcher matcher = TERM_PATTERN.matcher(term);
            if (!matcher.matches()) {
                throw new IllegalArgumentException("Unsupported sort expression");
            }
            String qualifier = Objects.requireNonNullElse(matcher.group(1), "");
            String column = matcher.group(2);
            String direction = matcher.group(3);
            terms.add(qualifier + column
                    + (direction == null ? "" : " " + direction.toLowerCase(Locale.ROOT)));
        }
        return new SafeSort(String.join(", ", terms));
    }

    public SafeSort withoutQualifier(String qualifier) {
        if (qualifier == null || qualifier.isBlank()) {
            return this;
        }
        String prefix = qualifier.endsWith(".") ? qualifier : qualifier + ".";
        return SafeSort.of(sql.replace(prefix, ""));
    }

    @Override
    public String toString() {
        return sql;
    }
}
