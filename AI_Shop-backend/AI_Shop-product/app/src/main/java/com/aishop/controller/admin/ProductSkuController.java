package com.aishop.controller.admin;

import com.aishop.entity.query.ProductSkuQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.ProductSkuService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("productSkuController")
@RequestMapping("/admin/productSku")
public class ProductSkuController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private ProductSkuService productSkuService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductSkuQuery query){
		return getSuccessResponseVO(productSkuService.findListByPage(query));
	}

	@PostMapping("/getProductSkuByProductIdAndPropertyValueIdHash")
	public ResponseVO getProductSkuByProductIdAndPropertyValueIdHash(String productId,String propertyValueIdHash) {
		return getSuccessResponseVO(productSkuService.getProductSkuByProductIdAndPropertyValueIdHash(productId,propertyValueIdHash));
	}

}
