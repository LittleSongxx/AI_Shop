package com.myshop.biz.impl;

import java.util.List;

import com.myshop.exception.BusinessException;
import jakarta.annotation.Resource;

import org.springframework.stereotype.Service;

import com.myshop.entity.enums.PageSize;
import com.myshop.entity.query.UserAddressQuery;
import com.myshop.entity.po.UserAddress;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.query.SimplePage;
import com.myshop.mappers.UserAddressMapper;
import com.myshop.biz.UserAddressService;
import com.myshop.utils.StringTools;

@Service("userAddressService")
public class UserAddressServiceImpl implements UserAddressService {

	@Resource
	private UserAddressMapper<UserAddress, UserAddressQuery> userAddressMapper;

	@Override
	public List<UserAddress> findListByParam(UserAddressQuery param) {
		return this.userAddressMapper.selectList(param);
	}

	@Override
	public Integer findCountByParam(UserAddressQuery param) {
		return this.userAddressMapper.selectCount(param);
	}

	@Override
	public PaginationResultVO<UserAddress> findListByPage(UserAddressQuery param) {
		int count = this.findCountByParam(param);
		int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();

		SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
		param.setSimplePage(page);
		List<UserAddress> list = this.findListByParam(param);
		PaginationResultVO<UserAddress> result = new PaginationResultVO(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
		return result;
	}

	@Override
	public Integer add(UserAddress bean) {
		return this.userAddressMapper.insert(bean);
	}

	@Override
	public Integer addBatch(List<UserAddress> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.userAddressMapper.insertBatch(listBean);
	}

	@Override
	public Integer addOrUpdateBatch(List<UserAddress> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.userAddressMapper.insertOrUpdateBatch(listBean);
	}

	@Override
	public Integer updateByParam(UserAddress bean, UserAddressQuery param) {
		StringTools.checkParam(param);
		return this.userAddressMapper.updateByParam(bean, param);
	}

	@Override
	public Integer deleteByParam(UserAddressQuery param) {
		StringTools.checkParam(param);
		return this.userAddressMapper.deleteByParam(param);
	}

	@Override
	public UserAddress getUserAddressByAddressId(String addressId) {
		return this.userAddressMapper.selectByAddressId(addressId);
	}

	@Override
	public Integer updateUserAddressByAddressId(UserAddress bean, String addressId) {
		return this.userAddressMapper.updateByAddressId(bean, addressId);
	}

	@Override
	public Integer deleteUserAddressByAddressId(String addressId) {
		return this.userAddressMapper.deleteByAddressId(addressId);
	}

	// 新增或修改地址
	@Override
	public void saveAddress(UserAddress userAddress) {
		Boolean isAdd = userAddress.getAddressId() == null || userAddress.getAddressId().isEmpty();
		// 如果是新增，则生成15位的随机字符串
		if (isAdd) {
			// 已有userId
			userAddress.setAddressId(StringTools.getRandomString(15));
			// 若default_type为null，则设置默认值为0
			if (userAddress.getDefaultType() == null) {
				userAddress.setDefaultType(0);
			}
			// 如果default_type为1，则将所有其他地址的default_type设置为0
			if (userAddress.getDefaultType() == 1) {
				cleanDefaultType(userAddress.getUserId());
			}
			this.add(userAddress);
		}else {
			// 否则为修改
			// 如果default_type为1，则将所有其他地址的default_type设置为0
			if (userAddress.getDefaultType() == 1) {
				cleanDefaultType(userAddress.getUserId());
			}
			this.updateUserAddressByAddressId(userAddress, userAddress.getAddressId());
		}
	}

	// 设置地址为默认
	@Override
	public void updateDefault(String userId, String addressId) {
		// 查询当前addressId是否属于该用户
		UserAddress userAddress = userAddressMapper.selectByAddressId(addressId);
		if (userAddress == null || !userAddress.getUserId().equals(userId)) {
			throw new BusinessException("当前地址不属于该用户");
		}
		// 修改该地址的default_type为1，并且将所有其他地址的default_type设置为0
		cleanDefaultType(userAddress.getUserId());
		userAddressMapper.updateDefaultType(addressId);
	}

	// 删除地址
	@Override
	public void deleteUserAddress(String userId, String addressId) {
		// 查询当前addressId是否属于该用户
		UserAddress userAddress = userAddressMapper.selectByAddressId(addressId);
		if (userAddress == null || !userAddress.getUserId().equals(userId)) {
			throw new  BusinessException("当前地址不属于该用户");
		}
		this.deleteUserAddressByAddressId(addressId);
	}

	private void cleanDefaultType(String userId){
		// 将当前userId的所有地址的default_type设置为0
		userAddressMapper.cleanAllDefaultType(userId);
	}
}
