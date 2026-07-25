package com.aishop.biz;

import java.util.List;

import com.aishop.entity.query.UserCouponQuery;
import com.aishop.entity.po.UserCoupon;
import com.aishop.entity.vo.PaginationResultVO;

public interface UserCouponService {

	List<UserCoupon> findListByParam(UserCouponQuery param);

	Integer findCountByParam(UserCouponQuery param);

	PaginationResultVO<UserCoupon> findListByPage(UserCouponQuery param);

	Integer add(UserCoupon bean);

	Integer addBatch(List<UserCoupon> listBean);

	Integer addOrUpdateBatch(List<UserCoupon> listBean);

	Integer updateByParam(UserCoupon bean,UserCouponQuery param);

	Integer deleteByParam(UserCouponQuery param);

	UserCoupon getUserCouponByUserCouponId(String userCouponId);

	Integer updateUserCouponByUserCouponId(UserCoupon bean,String userCouponId);

	Integer deleteUserCouponByUserCouponId(String userCouponId);

}
