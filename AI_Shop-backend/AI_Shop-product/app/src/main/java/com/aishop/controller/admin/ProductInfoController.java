package com.aishop.controller.admin;

import com.aishop.annotation.AdminSensitiveConfirm;
import com.aishop.entity.dto.ProductSaveDTO;
import com.aishop.entity.query.ProductInfoQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.ProductInfoService;
import com.aishop.biz.ProductSkuService;
import com.aishop.valid.Create;
import com.aishop.valid.Update;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("productInfoController")
@RequestMapping("/admin/productInfo")
public class ProductInfoController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private ProductInfoService productInfoService;

	@Resource
	private ProductSkuService productSkuService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(ProductInfoQuery query){
		return getSuccessResponseVO(productInfoService.findListByPage(query));
	}

	@PostMapping("/addProduct")
	public ResponseVO addProduct(@RequestBody @Validated(Create.class) ProductSaveDTO productSaveDTO) {
		productInfoService.saveProduct(productSaveDTO);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/updateProduct")
	@AdminSensitiveConfirm
	public ResponseVO updateProduct(@RequestBody @Validated(Update.class) ProductSaveDTO productSaveDTO) {
		productInfoService.saveProduct(productSaveDTO);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/loadProduct")
	public ResponseVO loadProduct(String productNameFuzzy,
								  Integer pageNo,
								  Integer pageSize,
								  String categoryIdOrPCategoryId,
								  Integer status,
								  Integer commendType) {
		ProductInfoQuery query = new ProductInfoQuery();
		query.setProductNameFuzzy(productNameFuzzy);
		query.setPageNo(pageNo);
		query.setPageSize(pageSize);
		query.setCategoryIdOrPCategoryId(categoryIdOrPCategoryId);
		query.setStatus(status);
		query.setCommendType(commendType);
		query.setOrderBy(com.aishop.entity.query.SafeSort.of("create_time desc"));
		PaginationResultVO resultVO = productInfoService.findListByPage4ListVO(query);
		return getSuccessResponseVO(resultVO);
	}

	@PostMapping("/getProductInfo")
	public ResponseVO getProductInfo(@NotEmpty String productId) {
		return getSuccessResponseVO(productInfoService.getProduct4VOByProductId(productId));
	}

	@PostMapping("/updateSkuStock")
	@AdminSensitiveConfirm
	public ResponseVO updateSkuStock(@NotEmpty String productId, @NotEmpty String propertyValueIdHash, @NotNull Integer changeStock) {
		productSkuService.updateStock(productId, propertyValueIdHash, changeStock);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/updateProductStatus")
	@AdminSensitiveConfirm
	public ResponseVO updateProductStatus(@NotNull String productId, @NotNull Integer status) {
		productInfoService.updateProductStatus(productId,status);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteProduct")
	@AdminSensitiveConfirm
	public ResponseVO deleteProduct(@NotNull String productId) {
		productInfoService.deleteProduct(productId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/commendProduct")
	public ResponseVO commendProduct(@NotNull String productId, @NotNull Integer commendType) {
		productInfoService.commendProduct(productId,commendType);
		return getSuccessResponseVO(null);
	}
}
