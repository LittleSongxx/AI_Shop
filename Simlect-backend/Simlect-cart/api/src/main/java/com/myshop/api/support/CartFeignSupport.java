package com.myshop.api.support;

import com.myshop.api.CartFeignClient;
import com.myshop.api.dto.CartDeleteBatchDTO;
import com.myshop.api.dto.CartDeleteItemDTO;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class CartFeignSupport {

    @Resource
    private CartFeignClient cartFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public void deleteBatch(List<CartDeleteItemDTO> items) {
        if (items == null || items.isEmpty()) {
            return;
        }
        feignResponseSupport.run(
                () -> cartFeignClient.deleteBatch(new CartDeleteBatchDTO(items)),
                "清理购物车失败");
    }
}
