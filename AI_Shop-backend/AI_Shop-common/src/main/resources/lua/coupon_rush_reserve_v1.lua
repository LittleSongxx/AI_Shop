-- 优惠券抢购预占：库存计数、参与者 SET、用户预占 hash 三个键必须一起改
-- KEYS: 无（键名由 couponId/userId 在脚本内拼出，与 Constants.REDIS_KEY_RUSHING_* 保持一致）
-- ARGV[1] couponId
-- ARGV[2] userId
-- ARGV[3] userCouponId
-- ARGV[4] 用户预占 hash 的过期秒数
-- 返回 0=预占成功 1=已抢完/券不存在 2=该用户已有有效预占
local couponId = ARGV[1];
local userId = ARGV[2];
local userCouponId = ARGV[3];
local stockKey = 'mall:rushing:stock:' .. couponId;
local couponKey = 'mall:rushing:coupon:' .. couponId;
local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId;
local stock = redis.call('get', stockKey);
if not stock then return 1 end;
local stockNum = tonumber(stock);
-- -1 表示不限量
if stockNum ~= -1 and stockNum <= 0 then return 1 end;
if redis.call('sismember', couponKey, userId) == 1 then
  -- 只有预占 hash 还在才算重复抢；hash 过期说明上一轮预占已失效
  if redis.call('exists', userCouponKey) == 1 then return 2 end;
  -- 预占 hash 已过期，清掉 SET 里的脏成员，否则该用户再也抢不到
  redis.call('srem', couponKey, userId);
end;
if stockNum ~= -1 then redis.call('decr', stockKey); end;
redis.call('sadd', couponKey, userId);
redis.call('hset', userCouponKey, 'couponId', couponId);
redis.call('hset', userCouponKey, 'userCouponId', userCouponId);
redis.call('hset', userCouponKey, 'time', redis.call('time')[1]);
redis.call('expire', userCouponKey, tonumber(ARGV[4]));
return 0;
