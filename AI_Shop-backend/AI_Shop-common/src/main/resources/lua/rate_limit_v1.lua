-- 固定窗口计数限流
-- 只在计数从 0 变 1 时设置过期时间，避免每次请求都续期导致窗口永不结束
-- KEYS[1] 计数 key
-- ARGV[1] 窗口秒数
-- ARGV[2] 窗口内允许的最大次数
-- 返回 1=放行 0=超限
local current = redis.call('INCR', KEYS[1]);
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end
if current > tonumber(ARGV[2]) then return 0 else return 1 end;
