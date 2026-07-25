package com.aishop.controller.admin;

import com.aishop.entity.query.SysProductPropertyQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.SysProductPropertyService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("sysProductPropertyController")
@RequestMapping("/admin/sysProductProperty")
public class SysProductPropertyController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private SysProductPropertyService sysProductPropertyService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(SysProductPropertyQuery query){
		return getSuccessResponseVO(sysProductPropertyService.findListByPage(query));
	}

	@PostMapping("/getSysProductPropertyByPropertyId")
	public ResponseVO getSysProductPropertyByPropertyId(String propertyId) {
		return getSuccessResponseVO(sysProductPropertyService.getSysProductPropertyByPropertyId(propertyId));
	}
}
