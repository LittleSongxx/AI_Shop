package com.aishop.controller.internal;

import com.aishop.controller.ABaseController;
import com.aishop.constants.Constants;
import com.aishop.api.enums.ProductStatusEnum;
import com.aishop.api.support.StockFeignSupport;
import com.aishop.entity.config.AppConfig;
import com.aishop.entity.po.ProductInfo;
import com.aishop.entity.po.ProductPropertyValue;
import com.aishop.entity.po.ProductSku;
import com.aishop.entity.query.ProductInfoQuery;
import com.aishop.entity.query.ProductPropertyValueQuery;
import com.aishop.entity.query.ProductSkuQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.mappers.ProductInfoMapper;
import com.aishop.mappers.ProductPropertyValueMapper;
import com.aishop.mappers.ProductSkuMapper;
import com.aishop.exception.BusinessException;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/product/agent")
public class ProductAgentInternalController extends ABaseController {

    @Resource
    private ProductInfoMapper<ProductInfo, ProductInfoQuery> productInfoMapper;
    @Resource
    private ProductSkuMapper<ProductSku, ProductSkuQuery> productSkuMapper;
    @Resource
    private ProductPropertyValueMapper<ProductPropertyValue, ProductPropertyValueQuery> productPropertyValueMapper;
    @Resource
    private StockFeignSupport stockFeignSupport;
    @Resource
    private AppConfig appConfig;

    @PostMapping("/searchOnSale")
    public ResponseVO<List<Map<String, Object>>> searchOnSale(@RequestBody Map<String, Object> body) {
        ProductInfoQuery query = new ProductInfoQuery();
        query.setStatus(ProductStatusEnum.ON_SALE.getStatus());
        String keyword = str(body, "keyword");
        if (!StringTools.isEmpty(keyword) && !keyword.startsWith("category:")) {
            query.setProductNameFuzzy(keyword);
        }
        String categoryId = str(body, "categoryId");
        if (StringTools.isEmpty(categoryId) && keyword != null && keyword.startsWith("category:")) {
            categoryId = keyword.substring("category:".length()).trim();
        }
        if (!StringTools.isEmpty(categoryId)) {
            query.setCategoryId(categoryId);
        }
        boolean hotSale = Boolean.TRUE.equals(body.get("hotSale"))
                || "true".equalsIgnoreCase(String.valueOf(body.get("hotSale")));
        // Column is total_sale (not sales); mapper aliases table as p.
        if (hotSale) {
            query.setOrderBy(com.aishop.entity.query.SafeSort.of("p.total_sale desc"));
        } else {
            query.setOrderBy(com.aishop.entity.query.SafeSort.of("p.create_time desc"));
        }
        int limit = intVal(body.get("limit"), 20);
        if (limit < 1) {
            limit = 1;
        }
        if (limit > 50) {
            limit = 50;
        }
        query.setSimplePage(new SimplePage(0, limit));
        List<ProductInfo> list = productInfoMapper.selectList(query);
        Map<String, String> brandByProduct = new HashMap<>();
        Map<String, Integer> stockByProduct = Collections.emptyMap();
        if (list != null && !list.isEmpty()) {
            List<String> productIds = new ArrayList<>();
            for (ProductInfo p : list) {
                productIds.add(p.getProductId());
            }
            stockByProduct = stockFeignSupport.totalByProducts(productIds);
            ProductPropertyValueQuery propertyQuery = new ProductPropertyValueQuery();
            propertyQuery.setProductIdList(productIds);
            List<ProductPropertyValue> properties = productPropertyValueMapper.selectList(propertyQuery);
            if (properties != null) {
                for (ProductPropertyValue property : properties) {
                    if (!brandByProduct.containsKey(property.getProductId())
                            && property.getPropertyName() != null
                            && property.getPropertyName().contains("品牌")
                            && !StringTools.isEmpty(property.getPropertyValue())) {
                        brandByProduct.put(property.getProductId(), property.getPropertyValue());
                    }
                }
            }
        }
        List<Map<String, Object>> result = new ArrayList<>();
        if (list != null) {
            for (ProductInfo p : list) {
                Map<String, Object> card = toAgentProductCard(p);
                String brand = brandByProduct.get(p.getProductId());
                if (!StringTools.isEmpty(brand)) {
                    card.put("brand", brand);
                }
                if (stockByProduct.containsKey(p.getProductId())) {
                    Integer totalStock = stockByProduct.get(p.getProductId());
                    card.put("totalStock", totalStock);
                    card.put("inStock", totalStock != null && totalStock > 0);
                }
                result.add(card);
            }
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/getDetail")
    public ResponseVO<Map<String, Object>> getDetail(@RequestBody Map<String, Object> body) {
        String productId = str(body, "productId");
        if (StringTools.isEmpty(productId)) {
            return getSuccessResponseVO(null);
        }
        ProductInfo p = productInfoMapper.selectByProductId(productId);
        if (p == null) {
            return getSuccessResponseVO(null);
        }
        Map<String, Object> m = toAgentProductCard(p);
        m.put("status", p.getStatus());
        m.put("maxPrice", p.getMaxPrice());
        m.put("description", p.getProductDesc());
        m.put("productDesc", p.getProductDesc());

        ProductSkuQuery skuQuery = new ProductSkuQuery();
        skuQuery.setProductId(productId);
        skuQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("sort asc"));
        List<ProductSku> skus = productSkuMapper.selectList(skuQuery);
        m.put("skus", skus == null ? Collections.emptyList() : skus);

        ProductPropertyValueQuery pvQuery = new ProductPropertyValueQuery();
        pvQuery.setProductId(productId);
        List<ProductPropertyValue> pvs = productPropertyValueMapper.selectList(pvQuery);
        m.put("propertyValues", pvs == null ? Collections.emptyList() : pvs);
        if (pvs != null) {
            for (ProductPropertyValue property : pvs) {
                if (property.getPropertyName() != null
                        && property.getPropertyName().contains("品牌")
                        && !StringTools.isEmpty(property.getPropertyValue())) {
                    m.put("brand", property.getPropertyValue());
                    break;
                }
            }
        }
        Map<String, Integer> stockByProduct = stockFeignSupport.totalByProducts(List.of(productId));
        if (stockByProduct.containsKey(productId)) {
            Integer totalStock = stockByProduct.get(productId);
            m.put("totalStock", totalStock);
            m.put("inStock", totalStock != null && totalStock > 0);
        }
        return getSuccessResponseVO(m);
    }

    /**
     * Authoritative SKU offer facts for the Agent.  This endpoint deliberately
     * returns a selected in-stock SKU and its base price; user coupons are
     * estimated by the Coupon service in a separate internal call.  The Agent
     * never reads product or stock tables directly.
     */
    @PostMapping("/offerSnapshots")
    public ResponseVO<Map<String, Object>> offerSnapshots(@RequestBody Map<String, Object> body) {
        Object rawIds = body == null ? null : body.get("productIds");
        if (!(rawIds instanceof List<?> ids) || ids.isEmpty()) {
            return getSuccessResponseVO(Map.of("products", List.of()));
        }
        List<Map<String, Object>> result = new ArrayList<>();
        int processed = 0;
        for (Object rawId : ids) {
            if (processed++ >= 20 || rawId == null) {
                break;
            }
            String productId = String.valueOf(rawId).trim();
            if (StringTools.isEmpty(productId)) {
                continue;
            }
            ProductInfo product = productInfoMapper.selectByProductId(productId);
            if (product == null) {
                continue;
            }
            ProductSkuQuery skuQuery = new ProductSkuQuery();
            skuQuery.setProductId(productId);
            skuQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("sort asc"));
            List<ProductSku> skus = productSkuMapper.selectList(skuQuery);
            Map<String, Object> row = toAgentProductCard(product);
            row.put("status", product.getStatus());
            row.put("categoryId", product.getCategoryId());
            row.put("cover", product.getCover());
            int totalStock = 0;
            Map<String, Object> selected = null;
            if (skus != null) {
                for (ProductSku sku : skus) {
                    if (sku == null || StringTools.isEmpty(sku.getPropertyValueIdHash())
                            || sku.getPrice() == null) {
                        continue;
                    }
                    int stock = Math.max(0, stockFeignSupport.getAvailable(
                            productId, sku.getPropertyValueIdHash()));
                    totalStock += stock;
                    if (stock <= 0 || !ProductStatusEnum.ON_SALE.getStatus().equals(product.getStatus())) {
                        continue;
                    }
                    if (selected == null || sku.getPrice().compareTo(
                            (java.math.BigDecimal) selected.get("price")) < 0) {
                        selected = new LinkedHashMap<>();
                        selected.put("propertyValueIdHash", sku.getPropertyValueIdHash());
                        selected.put("propertyValueIds", sku.getPropertyValueIds());
                        selected.put("price", sku.getPrice());
                        selected.put("stock", stock);
                    }
                }
            }
            row.put("totalStock", totalStock);
            row.put("inStock", selected != null);
            row.put("selectedSku", selected);
            result.add(row);
        }
        return getSuccessResponseVO(Map.of("products", result));
    }

    @PostMapping("/imageContent")
    public void imageContent(@RequestBody Map<String, Object> body, HttpServletResponse response)
            throws IOException {
        String productId = str(body, "productId");
        int coverIndex = intVal(body == null ? null : body.get("coverIndex"), 0);
        if (StringTools.isEmpty(productId) || coverIndex < 0 || coverIndex >= 5) {
            throw new BusinessException("商品图片参数不合法");
        }
        ProductInfo product = productInfoMapper.selectByProductId(productId);
        if (product == null || StringTools.isEmpty(product.getCover())) {
            throw new BusinessException("商品图片不存在");
        }
        List<String> covers = Arrays.stream(product.getCover().split(","))
                .map(String::trim)
                .filter(value -> !StringTools.isEmpty(value))
                .limit(5)
                .toList();
        if (coverIndex >= covers.size()) {
            throw new BusinessException("商品图片不存在");
        }
        String sourceName = covers.get(coverIndex);
        if (!StringTools.pathIsOK(sourceName)) {
            throw new BusinessException("商品图片路径不合法");
        }

        File baseFolder = new File(
                appConfig.getProjectFolder() + Constants.FILE_FOLDER_FILE).getCanonicalFile();
        File image = new File(baseFolder, sourceName).getCanonicalFile();
        if (!image.toPath().startsWith(baseFolder.toPath())
                || !image.isFile() || image.length() <= 0 || image.length() > 20L * 1024 * 1024) {
            throw new BusinessException("商品图片不可读取");
        }
        response.setContentType(resolveImageContentType(sourceName));
        response.setHeader("Cache-Control", "private, max-age=300");
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setContentLengthLong(image.length());
        try (FileInputStream input = new FileInputStream(image);
             OutputStream output = response.getOutputStream()) {
            input.transferTo(output);
        }
    }

    /** Agent 侧商品卡片的唯一销量字段。 */
    private static Map<String, Object> toAgentProductCard(ProductInfo p) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("productId", p.getProductId());
        m.put("productName", p.getProductName());
        m.put("cover", p.getCover());
        m.put("status", p.getStatus());
        m.put("minPrice", p.getMinPrice());
        m.put("maxPrice", p.getMaxPrice());
        m.put("categoryId", p.getCategoryId());
        m.put("totalSale", p.getTotalSale());
        m.put("commendType", p.getCommendType());
        return m;
    }

    private static String str(Map<String, Object> body, String key) {
        if (body == null || body.get(key) == null) {
            return null;
        }
        String v = String.valueOf(body.get(key));
        return "null".equals(v) ? null : v;
    }

    private static int intVal(Object v, int def) {
        if (v == null) {
            return def;
        }
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (Exception e) {
            return def;
        }
    }

    private static String resolveImageContentType(String sourceName) {
        String normalized = sourceName == null ? "" : sourceName.toLowerCase();
        if (normalized.endsWith(".png")) {
            return "image/png";
        }
        if (normalized.endsWith(".webp")) {
            return "image/webp";
        }
        if (normalized.endsWith(".gif")) {
            return "image/gif";
        }
        if (normalized.endsWith(".bmp")) {
            return "image/bmp";
        }
        return "image/jpeg";
    }
}
