package com.aishop.controller.admin;

import com.aishop.entity.query.UserAddressQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.UserAddressService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("adminUserAddressController")
@RequestMapping("/admin/userAddress")
public class UserAddressController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private UserAddressService userAddressService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(UserAddressQuery query){
		query.setOrderBy(com.aishop.entity.query.SafeSort.of("address_id desc"));
		return getSuccessResponseVO(userAddressService.findListByPage(query));
	}

	@PostMapping("/getUserAddressByAddressId")
	public ResponseVO getUserAddressByAddressId(String addressId) {
		return getSuccessResponseVO(userAddressService.getUserAddressByAddressId(addressId));
	}

	@PostMapping("/deleteUserAddressByAddressId")
	public ResponseVO deleteUserAddressByAddressId(String addressId) {
		userAddressService.deleteUserAddressByAddressId(addressId);
		return getSuccessResponseVO(null);
	}
}
