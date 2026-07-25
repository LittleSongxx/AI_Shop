package com.aishop.biz;

import com.aishop.api.dto.ProductInfoDTO;

import java.util.List;

public interface SearchRecommendService {

    List<String> loadGuessKeywords(String userId);

    List<ProductInfoDTO> loadRecommendProducts(String userId, int limit);
}
