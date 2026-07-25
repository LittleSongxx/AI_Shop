package com.aishop.utils;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

public final class RequestFingerprint {

    private RequestFingerprint() {
    }

    public static String sha256(Object request) {
        JsonNode canonical = canonicalize(JsonUtils.mapper().valueToTree(request));
        byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256")
                    .digest(JsonUtils.toJson(canonical).getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
        StringBuilder hex = new StringBuilder(digest.length * 2);
        for (byte value : digest) {
            hex.append(String.format("%02x", value));
        }
        return hex.toString();
    }

    private static JsonNode canonicalize(JsonNode node) {
        if (node == null || node.isNull() || node.isValueNode()) {
            return node;
        }
        if (node.isArray()) {
            ArrayNode array = JsonUtils.mapper().createArrayNode();
            for (JsonNode item : node) {
                array.add(canonicalize(item));
            }
            return array;
        }
        ObjectNode object = JsonUtils.mapper().createObjectNode();
        List<Map.Entry<String, JsonNode>> fields = new ArrayList<>();
        node.fields().forEachRemaining(fields::add);
        fields.sort(Comparator.comparing(Map.Entry::getKey));
        for (Map.Entry<String, JsonNode> field : fields) {
            object.set(field.getKey(), canonicalize(field.getValue()));
        }
        return object;
    }
}
