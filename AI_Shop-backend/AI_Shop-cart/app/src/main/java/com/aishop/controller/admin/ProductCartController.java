package com.aishop.controller.admin;

import com.aishop.entity.query.ProductCartQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.ProductCartService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("adminProductCartController")
@RequestMapping("/admin/productCart")
public class ProductCartController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private ProductCartService productCartService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductCartQuery query){
		return getSuccessResponseVO(productCartService.findListByPage(query));
	}

	@PostMapping("/getProductCartByCartId")
	public ResponseVO getProductCartByCartId(String cartId) {
		return getSuccessResponseVO(productCartService.getProductCartByCartId(cartId));
	}

	@PostMapping("/getProductCartByProductIdAndPropertyValueIdHashAndUserId")
	public ResponseVO getProductCartByProductIdAndPropertyValueIdHashAndUserId(String productId,String propertyValueIdHash,String userId) {
		return getSuccessResponseVO(productCartService.getProductCartByProductIdAndPropertyValueIdHashAndUserId(productId,propertyValueIdHash,userId));
	}

}
