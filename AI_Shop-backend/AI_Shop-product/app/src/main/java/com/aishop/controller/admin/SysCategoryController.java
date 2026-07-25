package com.aishop.controller.admin;

import com.aishop.entity.po.SysProductProperty;
import com.aishop.entity.query.SysCategoryQuery;
import com.aishop.entity.po.SysCategory;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.SysCategoryService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("sysCategoryController")
@RequestMapping("/admin/sysCategory")
public class SysCategoryController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private SysCategoryService sysCategoryService;

	@PostMapping("/loadCategory")
	public ResponseVO loadCategory(Boolean queryProperty){
		SysCategoryQuery query = new SysCategoryQuery();
		if (queryProperty!=null && queryProperty){
			query.setOrderBy(com.aishop.entity.query.SafeSort.of("s.sort asc"));
		}
		query.setPropertyQuery(queryProperty);
		query.setParent(true);
		return getSuccessResponseVO(sysCategoryService.findListByParam(query));
	}

	@PostMapping("/saveCategory")
	public ResponseVO saveCategory(SysCategory bean) {
		sysCategoryService.saveCategory(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getSysCategoryByCategoryId")
	public ResponseVO getSysCategoryByCategoryId(String categoryId) {
		return getSuccessResponseVO(sysCategoryService.getSysCategoryByCategoryId(categoryId));
	}

	@PostMapping("/delCategory")
	public ResponseVO delCategory(SysCategory bean) {
		sysCategoryService.deleteSysCategory(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/changeCategorySort")
	public ResponseVO changeCategorySort(String categoryIds) {
		sysCategoryService.changeCategorySort(categoryIds);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/saveProductProperty")
	public ResponseVO saveProductProperty(SysProductProperty bean) {
		sysCategoryService.saveProductProperty(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/delProductProperty")
	public ResponseVO delProductProperty(SysProductProperty bean) {
		sysCategoryService.delProductProperty(bean);
		return getSuccessResponseVO(null);
	}
}
