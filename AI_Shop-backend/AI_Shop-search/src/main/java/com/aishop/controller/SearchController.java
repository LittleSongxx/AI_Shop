package com.aishop.controller;

import com.aishop.annotation.GlobalInterceptor;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.entity.enums.ProductSortKey;
import com.aishop.entity.enums.SortDirection;
import com.aishop.biz.SearchKeywordService;
import com.aishop.biz.SearchRecommendService;
import com.aishop.component.EsSearchComponent;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;

@RequestMapping("/search")
@RestController
public class SearchController extends ABaseController {

    @Resource
    private SearchKeywordService searchKeywordService;
    @Resource
    private SearchRecommendService searchRecommendService;
    @Resource
    private EsSearchComponent esSearchComponent;

    @PostMapping("/products")
    public ResponseVO searchProducts(@NotEmpty String keyWords,
                                     String categoryId,
                                     BigDecimal priceFrom,
                                     BigDecimal priceTo,
                                     ProductSortKey sortKey,
                                     SortDirection sortDirection,
                                     Integer pageNo) {
        return getSuccessResponseVO(esSearchComponent.searchProducts(
                keyWords, categoryId, priceFrom, priceTo, sortKey, sortDirection, pageNo));
    }

    @GetMapping("/loadHotKeywords")
    public ResponseVO loadHotKeywords() {
        return getSuccessResponseVO(searchKeywordService.loadHotKeywords());
    }

    @GetMapping("/loadRecentKeywords")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadRecentKeywords() {
        return getSuccessResponseVO(searchKeywordService.loadRecentKeywords(getTokenUserInfo().getUserId()));
    }

    @PostMapping("/saveKeyword")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO saveKeyword(@NotEmpty String keyword) {
        searchKeywordService.saveUserKeyword(getTokenUserInfo().getUserId(), keyword);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/clearRecentKeywords")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO clearRecentKeywords() {
        searchKeywordService.clearUserKeywords(getTokenUserInfo().getUserId());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/removeRecentKeyword")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO removeRecentKeyword(@NotEmpty String keyword) {
        searchKeywordService.removeUserKeyword(getTokenUserInfo().getUserId(), keyword);
        return getSuccessResponseVO(null);
    }

    @GetMapping("/loadGuessKeywords")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadGuessKeywords() {
        return getSuccessResponseVO(searchRecommendService.loadGuessKeywords(getTokenUserInfo().getUserId()));
    }

    @GetMapping("/loadRecommendProducts")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadRecommendProducts(Integer limit) {
        int size = limit == null ? 8 : limit;
        return getSuccessResponseVO(searchRecommendService.loadRecommendProducts(getTokenUserInfo().getUserId(), size));
    }
}
