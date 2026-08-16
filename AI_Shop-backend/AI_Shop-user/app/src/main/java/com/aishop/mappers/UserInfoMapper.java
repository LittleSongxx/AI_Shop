package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;
import java.util.List;

public interface UserInfoMapper<T,P> extends BaseMapper<T,P> {

	List<String> selectAllUserId();

	List<T> selectBriefByUserIds(@Param("userIds") List<String> userIds);

	Integer applyTemporaryBan(
			@Param("userId") String userId,
			@Param("unbanAtMs") Long unbanAtMs);

	Integer clearTemporaryBanIfDue(
			@Param("userId") String userId,
			@Param("expectedUnbanAtMs") Long expectedUnbanAtMs,
			@Param("nowMs") Long nowMs);

	Integer clearTemporaryBanManually(@Param("userId") String userId);

	Integer clearTemporaryBanMarker(@Param("userId") String userId);

	Integer setPermanentBan(@Param("userId") String userId);

	List<String> selectExpiredTemporaryBanUserIds(
			@Param("nowMs") Long nowMs,
			@Param("limit") Integer limit);

	 Integer updateByUserId(@Param("bean") T t,@Param("userId") String userId);

	 Integer deleteByUserId(@Param("userId") String userId);

	 T selectByUserId(@Param("userId") String userId);

	 Integer updateByEmail(@Param("bean") T t,@Param("email") String email);

	 Integer deleteByEmail(@Param("email") String email);

	 T selectByEmail(@Param("email") String email);

	 Integer updateByNickName(@Param("bean") T t,@Param("nickName") String nickName);

	 Integer deleteByNickName(@Param("nickName") String nickName);

	 T selectByNickName(@Param("nickName") String nickName);

}
