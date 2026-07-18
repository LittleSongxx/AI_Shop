package com.myshop.biz;

import com.myshop.entity.dto.ProductInfoDTO;

import java.util.List;

public interface SearchRecommendService {

    List<String> loadGuessKeywords(String userId);

    List<ProductInfoDTO> loadRecommendProducts(String userId, int limit);
}
