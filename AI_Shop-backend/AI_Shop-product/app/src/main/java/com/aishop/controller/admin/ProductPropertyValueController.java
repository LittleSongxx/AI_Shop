package com.aishop.controller.admin;

import com.aishop.entity.query.ProductPropertyValueQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.ProductPropertyValueService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("productPropertyValueController")
@RequestMapping("/admin/productPropertyValue")
public class ProductPropertyValueController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private ProductPropertyValueService productPropertyValueService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductPropertyValueQuery query){
		return getSuccessResponseVO(productPropertyValueService.findListByPage(query));
	}

	@PostMapping("/getProductPropertyValueByProductIdAndPropertyValueId")
	public ResponseVO getProductPropertyValueByProductIdAndPropertyValueId(String productId,String propertyValueId) {
		return getSuccessResponseVO(productPropertyValueService.getProductPropertyValueByProductIdAndPropertyValueId(productId,propertyValueId));
	}

}
