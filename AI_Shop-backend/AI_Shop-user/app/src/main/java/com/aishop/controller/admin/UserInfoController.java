package com.aishop.controller.admin;

import com.aishop.entity.query.UserInfoQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.UserInfoService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("userInfoController")
@RequestMapping("/admin/userInfo")
public class UserInfoController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private UserInfoService userInfoService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(UserInfoQuery query){
		return getSuccessResponseVO(userInfoService.findListByPage(query));
	}

	@PostMapping("/getUserInfoByUserId")
	public ResponseVO getUserInfoByUserId(String userId) {
		return getSuccessResponseVO(userInfoService.getUserInfoByUserId(userId));
	}

	@PostMapping("/getUserInfoByEmail")
	public ResponseVO getUserInfoByEmail(String email) {
		return getSuccessResponseVO(userInfoService.getUserInfoByEmail(email));
	}

	@PostMapping("/getUserInfoByNickName")
	public ResponseVO getUserInfoByNickName(String nickName) {
		return getSuccessResponseVO(userInfoService.getUserInfoByNickName(nickName));
	}

}
