package com.aishop.component;

import co.elastic.clients.elasticsearch._types.query_dsl.Query;
import com.aishop.entity.enums.ProductSortKey;
import com.aishop.entity.enums.SortDirection;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.elasticsearch.client.elc.NativeQuery;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EsSearchSortTest {

    @Test
    void onlyTheSharedSortKeysProduceKnownIndexFields() {
        assertEquals(
                List.of("minPrice", "totalSale", "productId"),
                EsSearchComponent.buildSort(ProductSortKey.PRICE, SortDirection.ASC)
                        .stream().map(Sort.Order::getProperty).toList());
        assertEquals(
                List.of("totalSale", "minPrice", "productId"),
                EsSearchComponent.buildSort(ProductSortKey.SALE, SortDirection.DESC)
                        .stream().map(Sort.Order::getProperty).toList());
        assertEquals(
                List.of("_score", "totalSale", "productId"),
                EsSearchComponent.buildSort(null, null)
                        .stream().map(Sort.Order::getProperty).toList());
    }

    @Test
    void keywordSearchUsesAnalyzedNamePhraseAndDescriptionQueries() {
        NativeQuery searchQuery = EsSearchComponent.buildSearchQuery(
                " 降噪耳机 ", null, null, null, PageRequest.of(0, 15));
        Query query = searchQuery.getQuery();

        assertTrue(query.isBool());
        assertEquals("1", query.bool().minimumShouldMatch());
        assertEquals(
                List.of("productName", "productName", "productDesc"),
                query.bool().should().stream().map(EsSearchSortTest::queryField).toList());
        assertTrue(query.bool().should().get(0).isMatch());
        assertTrue(query.bool().should().get(1).isMatchPhrase());
        assertTrue(query.bool().should().get(2).isMatch());
        assertEquals(3.0F, query.bool().should().get(0).match().boost());
        assertEquals(5.0F, query.bool().should().get(1).matchPhrase().boost());
        assertEquals("降噪耳机", query.bool().should().get(0).match().query().stringValue());
    }

    @Test
    void categoryAndPriceConditionsAreNonScoringFilters() {
        NativeQuery searchQuery = EsSearchComponent.buildSearchQuery(
                "索尼", " 20003 ", new BigDecimal("1000"), new BigDecimal("5000"),
                PageRequest.of(1, 15));
        Query query = searchQuery.getQuery();

        assertTrue(query.isBool());
        assertEquals(3, query.bool().filter().size());
        assertEquals("categoryId", query.bool().filter().get(0).term().field());
        assertEquals("20003", query.bool().filter().get(0).term().value().stringValue());
        assertEquals("minPrice", query.bool().filter().get(1).range().number().field());
        assertEquals(1000.0, query.bool().filter().get(1).range().number().gte());
        assertEquals("maxPrice", query.bool().filter().get(2).range().number().field());
        assertEquals(5000.0, query.bool().filter().get(2).range().number().lte());
        assertEquals(1, searchQuery.getPageable().getPageNumber());
    }

    @Test
    void emptyConditionsProduceMatchAllQuery() {
        NativeQuery searchQuery = EsSearchComponent.buildSearchQuery(
                "  ", "", null, null, PageRequest.of(0, 15));

        assertTrue(searchQuery.getQuery().isMatchAll());
    }

    private static String queryField(Query query) {
        if (query.isMatch()) {
            return query.match().field();
        }
        if (query.isMatchPhrase()) {
            return query.matchPhrase().field();
        }
        throw new AssertionError("unexpected query type: " + query._kind());
    }
}
