package com.aishop.controller;

import com.aishop.annotation.GlobalInterceptor;
import com.aishop.entity.dto.TokenUserInfoDTO;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.ProductCart;
import com.aishop.entity.query.ProductCartQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.ProductCartVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.biz.ProductCartService;
import com.aishop.integration.RecommendationAttributionClient;
import com.aishop.integration.CommerceOutcomeClient;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;

@RequestMapping("/productCart")
@RestController
public class ProductCartController extends ABaseController{

    @Resource
    private ProductCartService productCartService;

    @Resource
    private RecommendationAttributionClient recommendationAttributionClient;

    @Resource
    private CommerceOutcomeClient commerceOutcomeClient;
    // 加入购物车
    @PostMapping("/add2Cart")
    @GlobalInterceptor(checkLogin = true)
    @Transactional(rollbackFor = Exception.class)
    public ResponseVO add2Cart(ProductCart productCart){
        TokenUserInfoDTO tokenUserInfo = getTokenUserInfo();
        if (tokenUserInfo == null || StringTools.isEmpty(tokenUserInfo.getUserId())) {
            throw new BusinessException("请先登录");
        }
        String userId = tokenUserInfo.getUserId();
        productCart.setUserId(userId);
        recommendationAttributionClient.validateAndApply(userId, List.of(productCart));
        int requestedQuantity = productCart.getBuyCount();
        ProductCart persisted = productCartService.add2Cart(productCart);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("quantity", requestedQuantity);
        if (persisted.getAddPrice() != null) {
            payload.put("unitPrice", persisted.getAddPrice());
        }
        payload.put("currency", "CNY");
        payload.put("cartItemId", persisted.getCartId());
        commerceOutcomeClient.recordAfterCommit(CommerceOutcomeClient.fromVerifiedCarrier(
                CommerceOutcomeClient.stableEventId(
                        "cart-add", userId, persisted.getCartId(), persisted.getLastUpdateTime(),
                        persisted.getBuyCount()),
                "CART",
                CommerceOutcomeClient.stableIdempotencyKey(
                        "cart-add", userId, persisted.getCartId(), persisted.getLastUpdateTime(),
                        persisted.getBuyCount()),
                "ADD_TO_CART",
                userId,
                productCart,
                persisted.getPropertyValueIdHash(),
                null,
                payload,
                persisted.getLastUpdateTime()));
        return getSuccessResponseVO(null);
    }

    // 获取购物车列表
    @PostMapping("/loadProductCart")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadProductCart(@NotNull Integer pageNo){
        TokenUserInfoDTO tokenUserInfo = getTokenUserInfo();
        if (tokenUserInfo == null || StringTools.isEmpty(tokenUserInfo.getUserId())) {
            throw new BusinessException("请先登录");
        }
        String userId = tokenUserInfo.getUserId();
        SimplePage page = new SimplePage(pageNo, PageSize.SIZE15.getSize());
        ProductCartQuery param = new ProductCartQuery();
        param.setSimplePage(page);
        // 按最后操作时间降序排序
        param.setOrderBy(com.aishop.entity.query.SafeSort.of("last_update_time desc"));
        PaginationResultVO<ProductCartVO> result = productCartService.findListByPageAndUserId(param, userId);
        return getSuccessResponseVO(result);
    }

    // 移除购物车
    @PostMapping("/deleteCart")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO deleteCart(@NotEmpty String cartId){
        TokenUserInfoDTO tokenUserInfo = getTokenUserInfo();
        if (tokenUserInfo == null || StringTools.isEmpty(tokenUserInfo.getUserId())) {
            throw new BusinessException("请先登录");
        }
        String userId = tokenUserInfo.getUserId();
        ProductCartQuery param = new ProductCartQuery();
        param.setCartId(cartId);
        param.setUserId(userId);
        productCartService.deleteByParam(param);
        return getSuccessResponseVO(null);
    }
}
