-- 补签：额度校验和 bitmap 打点必须在同一次原子操作里，否则并发补签会超额
-- 额度规则：每满 30 天累计签到送 1 次补签机会
-- KEYS[1] 目标月份的签到 bitmap
-- KEYS[2] 签到统计 hash
-- ARGV[1] 目标日期在该月的 bit 偏移（dayOfMonth - 1）
-- ARGV[2] 空值缓存 key，补签成功后要删掉
-- 返回 1=补签成功 -1=该天已签到 -2=补签额度不足
local bitmapKey = KEYS[1];
local hashKey = KEYS[2];
local targetOffset = tonumber(ARGV[1]);
if redis.call('getbit', bitmapKey, targetOffset) == 1 then
    return -1;
end;
local totalDays = tonumber(redis.call('hget', hashKey, 'totalSignDays') or '0');
local usedCount = tonumber(redis.call('hget', hashKey, 'usedCount') or '0');
if usedCount >= math.floor(totalDays / 30) then
    return -2;
end;
redis.call('setbit', bitmapKey, targetOffset, 1);
redis.call('hincrby', hashKey, 'usedCount', 1);
redis.call('hincrby', hashKey, 'totalSignDays', 1);
redis.call('del', ARGV[2]);
return 1;
