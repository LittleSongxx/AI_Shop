-- 支付订单生命周期锁的释放：只有持锁者能删自己的锁
-- 先 get 再 del 分两步会有窗口：锁刚过期、别人已重新加锁，此时 del 会删掉别人的锁
-- KEYS[1] 锁 key
-- ARGV[1] 加锁时写入的 token
-- 返回 1=已释放 0=token 不匹配（锁已不属于自己，什么都不做）
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
