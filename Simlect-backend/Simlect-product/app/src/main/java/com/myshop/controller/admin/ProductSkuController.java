package com.myshop.controller.admin;

import java.util.List;

import com.myshop.entity.query.ProductSkuQuery;
import com.myshop.entity.po.ProductSku;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.ProductSkuService;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("productSkuController")
@RequestMapping("/admin/productSku")
public class ProductSkuController extends com.myshop.controller.admin.ABaseController{

	@Resource
	private ProductSkuService productSkuService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductSkuQuery query){
		return getSuccessResponseVO(productSkuService.findListByPage(query));
	}

	@PostMapping("/add")
	public ResponseVO add(ProductSku bean) {
		productSkuService.add(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addBatch")
	public ResponseVO addBatch(@RequestBody List<ProductSku> listBean) {
		productSkuService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addOrUpdateBatch")
	public ResponseVO addOrUpdateBatch(@RequestBody List<ProductSku> listBean) {
		productSkuService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getProductSkuByProductIdAndPropertyValueIdHash")
	public ResponseVO getProductSkuByProductIdAndPropertyValueIdHash(String productId,String propertyValueIdHash) {
		return getSuccessResponseVO(productSkuService.getProductSkuByProductIdAndPropertyValueIdHash(productId,propertyValueIdHash));
	}

	@PostMapping("/updateProductSkuByProductIdAndPropertyValueIdHash")
	public ResponseVO updateProductSkuByProductIdAndPropertyValueIdHash(ProductSku bean,String productId,String propertyValueIdHash) {
		productSkuService.updateProductSkuByProductIdAndPropertyValueIdHash(bean,productId,propertyValueIdHash);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteProductSkuByProductIdAndPropertyValueIdHash")
	public ResponseVO deleteProductSkuByProductIdAndPropertyValueIdHash(String productId,String propertyValueIdHash) {
		productSkuService.deleteProductSkuByProductIdAndPropertyValueIdHash(productId,propertyValueIdHash);
		return getSuccessResponseVO(null);
	}
}
