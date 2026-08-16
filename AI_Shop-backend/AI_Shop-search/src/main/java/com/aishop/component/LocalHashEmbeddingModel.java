package com.aishop.component;

import org.springframework.ai.document.Document;
import org.springframework.ai.embedding.Embedding;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.ai.embedding.EmbeddingRequest;
import org.springframework.ai.embedding.EmbeddingResponse;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Deterministic local embedding model for development and integration tests.
 *
 * <p>This model preserves lexical similarity through hashed word and character
 * n-grams. It is intentionally not a replacement for a production semantic
 * embedding model.</p>
 */
public class LocalHashEmbeddingModel implements EmbeddingModel {

    private static final long FNV_OFFSET_BASIS = 0xcbf29ce484222325L;
    private static final long FNV_PRIME = 0x100000001b3L;

    private final int dimensions;

    public LocalHashEmbeddingModel(int dimensions) {
        if (dimensions < 64) {
            throw new IllegalArgumentException("Local embedding dimensions must be at least 64");
        }
        this.dimensions = dimensions;
    }

    @Override
    public EmbeddingResponse call(EmbeddingRequest request) {
        List<String> instructions = request.getInstructions();
        List<Embedding> embeddings = new ArrayList<>(instructions.size());
        for (int index = 0; index < instructions.size(); index++) {
            embeddings.add(new Embedding(embedText(instructions.get(index)), index));
        }
        return new EmbeddingResponse(embeddings);
    }

    @Override
    public float[] embed(Document document) {
        return embedText(document == null ? "" : document.getFormattedContent());
    }

    @Override
    public int dimensions() {
        return dimensions;
    }

    float[] embedText(String text) {
        float[] vector = new float[dimensions];
        String normalized = Normalizer.normalize(
                        text == null ? "" : text, Normalizer.Form.NFKC)
                .toLowerCase(Locale.ROOT)
                .trim();
        if (normalized.isEmpty()) {
            vector[0] = 1.0F;
            return vector;
        }

        String[] terms = normalized.split("[^\\p{L}\\p{N}]+");
        for (String term : terms) {
            if (term.isEmpty()) {
                continue;
            }
            addFeature(vector, "word:" + term, 2.0F);
            int[] codePoints = term.codePoints().toArray();
            addNgrams(vector, codePoints, 1, 0.35F);
            addNgrams(vector, codePoints, 2, 1.0F);
            addNgrams(vector, codePoints, 3, 0.7F);
        }
        normalize(vector);
        return vector;
    }

    private void addNgrams(float[] vector, int[] codePoints, int size, float weight) {
        if (codePoints.length < size) {
            return;
        }
        for (int start = 0; start <= codePoints.length - size; start++) {
            StringBuilder feature = new StringBuilder("char").append(size).append(':');
            for (int offset = 0; offset < size; offset++) {
                feature.appendCodePoint(codePoints[start + offset]);
            }
            addFeature(vector, feature.toString(), weight);
        }
    }

    private void addFeature(float[] vector, String feature, float weight) {
        long hash = FNV_OFFSET_BASIS;
        for (int index = 0; index < feature.length(); index++) {
            hash ^= feature.charAt(index);
            hash *= FNV_PRIME;
        }
        int bucket = Math.floorMod((int) (hash ^ (hash >>> 32)), dimensions);
        vector[bucket] += (hash < 0 ? -weight : weight);
    }

    private void normalize(float[] vector) {
        double squaredNorm = 0.0D;
        for (float value : vector) {
            squaredNorm += value * value;
        }
        if (squaredNorm == 0.0D) {
            vector[0] = 1.0F;
            return;
        }
        float norm = (float) Math.sqrt(squaredNorm);
        for (int index = 0; index < vector.length; index++) {
            vector[index] /= norm;
        }
    }
}
