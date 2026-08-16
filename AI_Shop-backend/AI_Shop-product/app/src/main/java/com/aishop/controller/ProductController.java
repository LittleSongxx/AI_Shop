package com.aishop.controller;

import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.api.dto.BrowseHistoryMessageDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.ProductSortKey;
import com.aishop.entity.enums.SortDirection;
import com.aishop.entity.query.ProductInfoQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.query.SysCategoryQuery;
import com.aishop.entity.vo.Product4VO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.entity.dto.TokenUserInfoDTO;
import com.aishop.biz.ProductInfoService;
import com.aishop.biz.SysCategoryService;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotNull;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;

@RestController
@RequestMapping("/product")
@Validated
public class ProductController extends ABaseController {

    @Resource
    private SysCategoryService sysCategoryService;

    @Resource
    private ProductInfoService productInfoService;

    @Resource
    private ReliableMessageSender reliableMessageSender;

    @GetMapping("/loadCategory")
    public ResponseVO loadCategory() {
        SysCategoryQuery query = new SysCategoryQuery();
        query.setParent(true);
        query.setPropertyQuery(true);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("sort asc"));
        return getSuccessResponseVO(sysCategoryService.findListByParam(query));
    }

    @GetMapping("/loadCommendProduct")
    public ResponseVO loadCommendProduct() {
        ProductInfoQuery query = new ProductInfoQuery();
        query.setCommendType(1);
        query.setStatus(1);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("create_time desc"));
        query.setSimplePage(new SimplePage(0, 11));
        return getSuccessResponseVO(productInfoService.findListByPage(query).getList());
    }

    @PostMapping("/loadProduct")
    public ResponseVO loadProduct(@NotNull Integer pageNo,
                                  String categoryId,
                                  BigDecimal priceFrom,
                                  BigDecimal priceTo,
                                  ProductSortKey sortKey,
                                  SortDirection sortDirection) {
        ProductInfoQuery query = new ProductInfoQuery();
        query.setPageNo(pageNo);
        query.setStatus(com.aishop.api.enums.ProductStatusEnum.ON_SALE.getStatus());
        query.setPriceFrom(priceFrom);
        query.setPriceTo(priceTo);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of(buildProductOrderBy(sortKey, sortDirection)));
        if (categoryId == null) {
            query.setCommendType(0);
            return getSuccessResponseVO(productInfoService.findListByPage(query));
        }
        query.setCategoryIdOrPCategoryId(categoryId);
        return getSuccessResponseVO(productInfoService.findListByPage(query));
    }

    private String buildProductOrderBy(ProductSortKey sortKey, SortDirection sortDirection) {
        if (ProductSortKey.PRICE.equals(sortKey)) {
            return SortDirection.ASC.equals(sortDirection)
                    ? "min_price asc, total_sale desc"
                    : "min_price desc, total_sale desc";
        }
        if (ProductSortKey.SALE.equals(sortKey)) {
            return "total_sale desc, min_price asc";
        }
        return "commend_type desc, total_sale desc, create_time desc";
    }

    @PostMapping("/getProduct")
    public ResponseVO getProduct(@NotNull String productId) {
        Product4VO product = productInfoService.getProduct4VOByProductId(productId);
        TokenUserInfoDTO tokenUserInfo = getTokenUserInfo();
        if (tokenUserInfo != null && tokenUserInfo.getUserId() != null) {
            BrowseHistoryMessageDTO message = new BrowseHistoryMessageDTO();
            message.setUserId(tokenUserInfo.getUserId());
            message.setProductId(productId);
            long browseTime = System.currentTimeMillis();
            message.setBrowseTime(browseTime);
            reliableMessageSender.sendMessage(
                    RabbitMQConfig.BROWSE_EXCHANGE,
                    RabbitMQConfig.BROWSE_RECORD_KEY,
                    message,
                    MqIdempotencyKeys.browseRecord(
                            tokenUserInfo.getUserId(), productId, browseTime),
                    MessageReliabilityLevelEnum.HIGH);
        }
        return getSuccessResponseVO(product);
    }
}
