-- 每日签到：bitmap 打点 + 连续天数递推 + 总天数累加，必须原子
-- KEYS[1] 本月签到 bitmap
-- KEYS[2] 昨天所在月份的 bitmap（跨月时与 KEYS[1] 不同）
-- KEYS[3] 签到统计 hash
-- ARGV[1] 今天在本月的 bit 偏移（dayOfMonth - 1）
-- ARGV[2] 昨天在其所在月份的 bit 偏移
-- ARGV[3] 空值缓存 key，签到成功后要删掉
-- 返回 {1, continuousDays, totalDays}；已签到返回 {-1, 0, 0}
local todayKey = KEYS[1];
local yesterdayKey = KEYS[2];
local hashKey = KEYS[3];
local todayOffset = tonumber(ARGV[1]);
local yesterdayOffset = tonumber(ARGV[2]);
if redis.call('getbit', todayKey, todayOffset) == 1 then return {-1, 0, 0} end;
redis.call('setbit', todayKey, todayOffset, 1);
local yesterdaySigned = redis.call('getbit', yesterdayKey, yesterdayOffset);
local continuousDays = 1;
if yesterdaySigned == 1 then
    local current = redis.call('hget', hashKey, 'continuousDays');
    if current then continuousDays = tonumber(current) + 1 end;
end;
redis.call('hset', hashKey, 'continuousDays', continuousDays);
local totalDays = redis.call('hincrby', hashKey, 'totalSignDays', 1);
redis.call('del', ARGV[3]);
return {1, continuousDays, totalDays};
