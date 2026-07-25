-- 清理参与者 SET 里的僵尸成员：预占 hash 已过期，成员却还留在 SET 里
--
-- 为什么会残留：预占 hash 有 TTL 会自己过期，SET 没有 TTL。正常路径靠回滚脚本
-- 或该用户下一次抢购时的懒清理摘掉成员；但用户不再来、且回滚消息丢了的时候，
-- 成员就永久留在 SET 里。SET 只增不减，参与人数会越统计越大。
--
-- 判定条件与 coupon_rush_reserve_v1.lua 里的懒清理完全一致（hash 不存在即视为
-- 预占已失效），所以这里只是把那件事提前做掉，不改变任何业务判定。
--
-- 必须在 Lua 里"查 hash + 摘成员"一起做：分两次往返的话，中间用户重新抢购写回
-- 新 hash，就会把有效预占的成员误删，该用户的资格随之丢失。
--
-- KEYS: 无（键名由 couponId/userId 拼出，与 Constants.REDIS_KEY_RUSHING_* 一致）
-- ARGV[1]  couponId
-- ARGV[2..n] 待检查的 userId（由调用方分批传入，避免单次脚本执行过久阻塞 Redis）
-- 返回 被摘掉的成员数
local couponId = ARGV[1];
local couponKey = 'mall:rushing:coupon:' .. couponId;
local removed = 0;
for i = 2, #ARGV do
  local userId = ARGV[i];
  local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId;
  if redis.call('exists', userCouponKey) == 0 then
    removed = removed + redis.call('srem', couponKey, userId);
  end;
end;
return removed;
