-- 预占回滚（只动 Redis）：数据库那一侧还没扣成功，把 Redis 的预占原样退回去
-- ARGV[1] couponId
-- ARGV[2] userId
-- 返回 0（无条件成功，回滚路径不应该再抛错把原始异常盖掉）
local couponId = ARGV[1];
local userId = ARGV[2];
local stockKey = 'mall:rushing:stock:' .. couponId;
local couponKey = 'mall:rushing:coupon:' .. couponId;
local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId;
local stock = redis.call('get', stockKey);
-- 不限量的券（-1）不需要还库存，否则会把哨兵值改成 0
if stock and tonumber(stock) ~= -1 then redis.call('incr', stockKey); end;
redis.call('srem', couponKey, userId);
redis.call('del', userCouponKey);
return 0;
