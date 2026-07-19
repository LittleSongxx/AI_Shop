package com.simlect.controller.internal;

import com.simlect.controller.ABaseController;
import com.simlect.api.enums.ProductStatusEnum;
import com.simlect.entity.po.ProductInfo;
import com.simlect.entity.po.ProductPropertyValue;
import com.simlect.entity.po.ProductSku;
import com.simlect.entity.query.ProductInfoQuery;
import com.simlect.entity.query.ProductPropertyValueQuery;
import com.simlect.entity.query.ProductSkuQuery;
import com.simlect.entity.query.SimplePage;
import com.simlect.entity.vo.ResponseVO;
import com.simlect.mappers.ProductInfoMapper;
import com.simlect.mappers.ProductPropertyValueMapper;
import com.simlect.mappers.ProductSkuMapper;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.Collections;
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
            query.setOrderBy("p.total_sale desc");
        } else {
            query.setOrderBy("p.create_time desc");
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
        List<Map<String, Object>> result = new ArrayList<>();
        if (list != null) {
            for (ProductInfo p : list) {
                result.add(toAgentProductCard(p));
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
        skuQuery.setOrderBy("sort asc");
        List<ProductSku> skus = productSkuMapper.selectList(skuQuery);
        m.put("skus", skus == null ? Collections.emptyList() : skus);

        ProductPropertyValueQuery pvQuery = new ProductPropertyValueQuery();
        pvQuery.setProductId(productId);
        List<ProductPropertyValue> pvs = productPropertyValueMapper.selectList(pvQuery);
        m.put("propertyValues", pvs == null ? Collections.emptyList() : pvs);
        return getSuccessResponseVO(m);
    }

    /** Agent 侧字段：totalSale 为主；保留 sales 兼容旧客户端。 */
    private static Map<String, Object> toAgentProductCard(ProductInfo p) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("productId", p.getProductId());
        m.put("productName", p.getProductName());
        m.put("cover", p.getCover());
        m.put("minPrice", p.getMinPrice());
        m.put("categoryId", p.getCategoryId());
        m.put("totalSale", p.getTotalSale());
        m.put("sales", p.getTotalSale());
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
}
