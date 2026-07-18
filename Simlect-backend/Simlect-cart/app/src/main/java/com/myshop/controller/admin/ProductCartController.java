package com.myshop.controller.admin;

import java.util.List;

import com.myshop.entity.query.ProductCartQuery;
import com.myshop.entity.po.ProductCart;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.ProductCartService;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("adminProductCartController")
@RequestMapping("/admin/productCart")
public class ProductCartController extends com.myshop.controller.admin.ABaseController{

	@Resource
	private ProductCartService productCartService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductCartQuery query){
		return getSuccessResponseVO(productCartService.findListByPage(query));
	}

	@PostMapping("/add")
	public ResponseVO add(ProductCart bean) {
		productCartService.add(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addBatch")
	public ResponseVO addBatch(@RequestBody List<ProductCart> listBean) {
		productCartService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addOrUpdateBatch")
	public ResponseVO addOrUpdateBatch(@RequestBody List<ProductCart> listBean) {
		productCartService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getProductCartByCartId")
	public ResponseVO getProductCartByCartId(String cartId) {
		return getSuccessResponseVO(productCartService.getProductCartByCartId(cartId));
	}

	@PostMapping("/updateProductCartByCartId")
	public ResponseVO updateProductCartByCartId(ProductCart bean,String cartId) {
		productCartService.updateProductCartByCartId(bean,cartId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteProductCartByCartId")
	public ResponseVO deleteProductCartByCartId(String cartId) {
		productCartService.deleteProductCartByCartId(cartId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getProductCartByProductIdAndPropertyValueIdHashAndUserId")
	public ResponseVO getProductCartByProductIdAndPropertyValueIdHashAndUserId(String productId,String propertyValueIdHash,String userId) {
		return getSuccessResponseVO(productCartService.getProductCartByProductIdAndPropertyValueIdHashAndUserId(productId,propertyValueIdHash,userId));
	}

	@PostMapping("/updateProductCartByProductIdAndPropertyValueIdHashAndUserId")
	public ResponseVO updateProductCartByProductIdAndPropertyValueIdHashAndUserId(ProductCart bean,String productId,String propertyValueIdHash,String userId) {
		productCartService.updateProductCartByProductIdAndPropertyValueIdHashAndUserId(bean,productId,propertyValueIdHash,userId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteProductCartByProductIdAndPropertyValueIdHashAndUserId")
	public ResponseVO deleteProductCartByProductIdAndPropertyValueIdHashAndUserId(String productId,String propertyValueIdHash,String userId) {
		productCartService.deleteProductCartByProductIdAndPropertyValueIdHashAndUserId(productId,propertyValueIdHash,userId);
		return getSuccessResponseVO(null);
	}
}
