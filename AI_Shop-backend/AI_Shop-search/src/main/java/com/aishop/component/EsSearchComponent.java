package com.aishop.component;

import co.elastic.clients.elasticsearch._types.query_dsl.BoolQuery;
import co.elastic.clients.elasticsearch._types.query_dsl.Query;
import com.aishop.api.support.ProductFeignSupport;
import com.aishop.api.vo.ProductSearchIndexVO;
import com.aishop.api.dto.ProductInfoDTO;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.enums.ProductSortKey;
import com.aishop.entity.enums.SortDirection;
import com.aishop.api.enums.ProductStatusEnum;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.utils.StringTools;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.elasticsearch.core.ElasticsearchOperations;
import org.springframework.data.elasticsearch.core.IndexOperations;
import org.springframework.data.elasticsearch.core.SearchHit;
import org.springframework.data.elasticsearch.core.SearchHits;
import org.springframework.data.elasticsearch.client.elc.NativeQuery;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.List;
import java.util.stream.Collectors;

@Component("esSearchComponent")
@Slf4j
public class EsSearchComponent {

    @Resource
    private ElasticsearchOperations elasticsearchOperations;

    @Resource
    private ProductFeignSupport productFeignSupport;

    @PostConstruct
    public void createIndexWithIK() {
        try {
            IndexOperations indexOperations = elasticsearchOperations.indexOps(ProductInfoDTO.class);
            if (indexOperations.exists()) {
                log.info("索引已存在，跳过创建");
                return;
            }
            Boolean success = indexOperations.createWithMapping();
            if (Boolean.TRUE.equals(success)) {
                log.info("索引创建成功（IK 分词）");
            } else {
                log.error("创建索引失败：ES 返回 false。请确认已安装 analysis-ik（镜像 aishop-elasticsearch:8.19.19-ik）");
            }
        } catch (Exception e) {
            log.error("创建索引失败（常见原因：ES 未安装 IK 插件 ik_max_word）。"
                    + "请重建中间件 ES：docker compose -f deploy/docker-compose.middleware.yml build elasticsearch --no-cache && up -d elasticsearch", e);
        }
    }

    // 保存索引
    public void saveIndex(String productId){
        ProductSearchIndexVO productInfo = productFeignSupport.getSearchIndex(productId);
        if (productInfo == null) {
            ProductInfoDTO deleteDto = new ProductInfoDTO();
            deleteDto.setProductId(productId);
            try {
                elasticsearchOperations.delete(deleteDto);
            } catch (Exception e) {
                log.warn("删除不存在商品的ES索引跳过 productId={}", productId);
            }
            return;
        }
        ProductInfoDTO productInfoDTO = new ProductInfoDTO();
        BeanUtils.copyProperties(productInfo, productInfoDTO);
        if (ProductStatusEnum.ON_SALE.getStatus().equals(productInfo.getStatus())){
            elasticsearchOperations.save(productInfoDTO);
        }else {
            elasticsearchOperations.delete(productInfoDTO);
        }
    }
    // 查询索引
    public PaginationResultVO<ProductInfoDTO> searchProducts(@NotEmpty String keyWords,
                                                             String categoryId,
                                                             BigDecimal priceFrom,
                                                             BigDecimal priceTo,
                                                             ProductSortKey sortKey,
                                                             SortDirection sortDirection,
                                                             Integer pageNo){
        try {
            // 参数处理
            pageNo = pageNo == null ? 1 : Math.max(pageNo, 1);
            //es 分页从0开始
            pageNo = pageNo - 1;
            int pageSize = PageSize.SIZE15.getSize();
            Sort sort = buildSort(sortKey, sortDirection);
            // 分页
            Pageable pageable = PageRequest.of(pageNo, pageSize, sort);
            // 执行查询
            NativeQuery searchQuery = buildSearchQuery(keyWords, categoryId, priceFrom, priceTo, pageable);
            SearchHits<ProductInfoDTO> searchHits = elasticsearchOperations.search(searchQuery, ProductInfoDTO.class);
            // 处理结果
            List<ProductInfoDTO> productInfoDTOList = searchHits.getSearchHits().stream().map(SearchHit::getContent).collect(Collectors.toList());
            long totalHits = searchHits.getTotalHits();
            int totalPage = (int) Math.ceil((double) totalHits / pageSize);
            return new PaginationResultVO<>((int) totalHits, pageSize, pageNo + 1, totalPage, productInfoDTOList);

        }catch (Exception e){
            log.error("搜索失败", e);
            throw new com.aishop.exception.BusinessException("搜索服务暂时不可用，请稍后重试");
        }
    }

    static NativeQuery buildSearchQuery(String keyWords,
                                        String categoryId,
                                        BigDecimal priceFrom,
                                        BigDecimal priceTo,
                                        Pageable pageable) {
        String normalizedKeywords = StringTools.isEmpty(keyWords) ? "" : keyWords.trim();
        String normalizedCategoryId = StringTools.isEmpty(categoryId) ? "" : categoryId.trim();

        boolean hasKeywords = !normalizedKeywords.isEmpty();
        boolean hasCategory = !normalizedCategoryId.isEmpty();
        boolean hasPrice = priceFrom != null || priceTo != null;

        Query query;
        if (!hasKeywords && !hasCategory && !hasPrice) {
            query = Query.of(q -> q.matchAll(matchAll -> matchAll));
        } else {
            BoolQuery.Builder boolQuery = new BoolQuery.Builder();

            if (hasKeywords) {
                boolQuery.should(Query.of(q -> q.match(match -> match
                        .field("productName")
                        .query(normalizedKeywords)
                        .boost(3.0F))));
                boolQuery.should(Query.of(q -> q.matchPhrase(matchPhrase -> matchPhrase
                        .field("productName")
                        .query(normalizedKeywords)
                        .boost(5.0F))));
                boolQuery.should(Query.of(q -> q.match(match -> match
                        .field("productDesc")
                        .query(normalizedKeywords))));
                boolQuery.minimumShouldMatch("1");
            }

            if (hasCategory) {
                boolQuery.filter(Query.of(q -> q.term(term -> term
                        .field("categoryId")
                        .value(normalizedCategoryId))));
            }
            if (priceFrom != null) {
                boolQuery.filter(Query.of(q -> q.range(range -> range.number(number -> number
                        .field("minPrice")
                        .gte(priceFrom.doubleValue())))));
            }
            if (priceTo != null) {
                boolQuery.filter(Query.of(q -> q.range(range -> range.number(number -> number
                        .field("maxPrice")
                        .lte(priceTo.doubleValue())))));
            }

            query = Query.of(q -> q.bool(boolQuery.build()));
        }

        return NativeQuery.builder()
                .withQuery(query)
                .withPageable(pageable)
                .build();
    }

    static Sort buildSort(ProductSortKey sortKey, SortDirection sortDirection) {
        ProductSortKey effectiveKey = sortKey == null ? ProductSortKey.COMPOSITE : sortKey;
        Sort.Direction direction = SortDirection.ASC.equals(sortDirection)
                ? Sort.Direction.ASC : Sort.Direction.DESC;
        return switch (effectiveKey) {
            case PRICE -> Sort.by(
                    new Sort.Order(direction, "minPrice"),
                    new Sort.Order(Sort.Direction.DESC, "totalSale"),
                    new Sort.Order(Sort.Direction.ASC, "productId"));
            case SALE -> Sort.by(
                    new Sort.Order(Sort.Direction.DESC, "totalSale"),
                    new Sort.Order(Sort.Direction.ASC, "minPrice"),
                    new Sort.Order(Sort.Direction.ASC, "productId"));
            case COMPOSITE -> Sort.by(
                    new Sort.Order(Sort.Direction.DESC, "_score"),
                    new Sort.Order(Sort.Direction.DESC, "totalSale"),
                    new Sort.Order(Sort.Direction.ASC, "productId"));
        };
    }
}
