package com.myshop.controller.internal;

import com.myshop.controller.ABaseController;
import com.myshop.entity.enums.ProductStatusEnum;
import com.myshop.entity.po.ProductInfo;
import com.myshop.entity.po.ProductPropertyValue;
import com.myshop.entity.po.ProductSku;
import com.myshop.entity.query.ProductInfoQuery;
import com.myshop.entity.query.ProductPropertyValueQuery;
import com.myshop.entity.query.ProductSkuQuery;
import com.myshop.entity.query.SimplePage;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.mappers.ProductInfoMapper;
import com.myshop.mappers.ProductPropertyValueMapper;
import com.myshop.mappers.ProductSkuMapper;
import com.myshop.utils.StringTools;
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
        if (hotSale) {
            query.setOrderBy("sales desc");
        } else {
            query.setOrderBy("create_time desc");
        }
        int limit = intVal(body.get("limit"), 20);
        query.setSimplePage(new SimplePage(0, limit));
        List<ProductInfo> list = productInfoMapper.selectList(query);
        List<Map<String, Object>> result = new ArrayList<>();
        if (list != null) {
            for (ProductInfo p : list) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("productId", p.getProductId());
                m.put("productName", p.getProductName());
                m.put("cover", p.getCover());
                m.put("minPrice", p.getMinPrice());
                m.put("categoryId", p.getCategoryId());
                m.put("sales", p.getTotalSale());
                result.add(m);
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
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("productId", p.getProductId());
        m.put("productName", p.getProductName());
        m.put("cover", p.getCover());
        m.put("minPrice", p.getMinPrice());
        m.put("categoryId", p.getCategoryId());
        m.put("status", p.getStatus());
        m.put("sales", p.getTotalSale());
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
