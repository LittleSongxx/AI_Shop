-- 释放预占并以数据库余量为准对齐 Redis 库存
-- 与 rollback 的区别：rollback 是 incr（相信 Redis 当前值），这里是 set（不相信 Redis，直接用库里的数）
-- ARGV[1] couponId
-- ARGV[2] userId
-- ARGV[3] 数据库剩余库存，-1 表示不限量
-- 返回 0
local couponId = ARGV[1];
local userId = ARGV[2];
local dbRemain = tonumber(ARGV[3]);
local stockKey = 'mall:rushing:stock:' .. couponId;
local couponKey = 'mall:rushing:coupon:' .. couponId;
local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId;
redis.call('set', stockKey, dbRemain);
redis.call('srem', couponKey, userId);
redis.call('del', userCouponKey);
return 0;
