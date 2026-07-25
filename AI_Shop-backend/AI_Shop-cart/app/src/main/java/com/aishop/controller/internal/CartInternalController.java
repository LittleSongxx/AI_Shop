package com.aishop.controller.internal;

import com.aishop.api.dto.CartDeleteBatchDTO;
import com.aishop.biz.CartInternalService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/cart")
public class CartInternalController extends ABaseController {

    @Resource
    private CartInternalService cartInternalService;

    @PostMapping("/deleteBatch")
    public ResponseVO<Void> deleteBatch(@RequestBody CartDeleteBatchDTO dto) {
        cartInternalService.deleteBatch(dto);
        return getSuccessResponseVO(null);
    }
}
