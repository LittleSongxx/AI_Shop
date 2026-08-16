package com.aishop.component;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

class LocalHashEmbeddingModelTest {

    private final LocalHashEmbeddingModel model = new LocalHashEmbeddingModel(1024);

    @Test
    void createsStableNormalizedVectorsWithConfiguredDimensions() {
        float[] first = model.embed("轻薄笔记本电脑");
        float[] second = model.embed("轻薄笔记本电脑");

        assertThat(first).hasSize(1024).containsExactly(second);
        assertThat(l2Norm(first)).isCloseTo(1.0D, within(0.00001D));
    }

    @Test
    void preservesMoreSimilarityForLexicallyRelatedChineseQueries() {
        float[] query = model.embed("轻薄笔记本");
        float[] related = model.embed("轻薄笔记本电脑 适合办公");
        float[] unrelated = model.embed("厨房不锈钢炒锅");

        assertThat(cosine(query, related)).isGreaterThan(cosine(query, unrelated));
    }

    @Test
    void keepsTheCrossLanguageSparseVectorContract() {
        float[] vector = model.embed("轻薄笔记本");
        List<Integer> nonZeroIndexes = new ArrayList<>();
        for (int index = 0; index < vector.length; index++) {
            if (vector[index] != 0.0F) {
                nonZeroIndexes.add(index);
            }
        }

        assertThat(nonZeroIndexes).containsExactly(
                50, 132, 172, 182, 187, 520, 573, 604, 627, 711, 773, 788, 821);
        assertThat(vector[50]).isCloseTo(-0.629862666F, within(0.0000001F));
        assertThat(vector[132]).isCloseTo(0.314931333F, within(0.0000001F));
    }

    private double l2Norm(float[] vector) {
        return Math.sqrt(cosineNumerator(vector, vector));
    }

    private double cosine(float[] left, float[] right) {
        return cosineNumerator(left, right) / (l2Norm(left) * l2Norm(right));
    }

    private double cosineNumerator(float[] left, float[] right) {
        double sum = 0.0D;
        for (int index = 0; index < left.length; index++) {
            sum += left[index] * right[index];
        }
        return sum;
    }
}
