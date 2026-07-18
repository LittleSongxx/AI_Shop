package com.myshop.redis;

import lombok.extern.slf4j.Slf4j;
import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.client.RedisConnectionException;
import org.redisson.config.Config;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@Slf4j
public class RedissionConfig {

    @Value("${spring.data.redis.host:}")
    private String redisHost;

    @Value("${spring.data.redis.port:}")
    private String redisPort;

    // 创建RedissonClient对象
    @Bean(value = "redissonClient",destroyMethod = "shutdown")
    public RedissonClient redissonClient() {
        try {
            // 创建RedissonClient对象
            Config config = new Config();
            config.useSingleServer().setAddress("redis://" + redisHost + ":" + redisPort);
            RedissonClient redissonClient = Redisson.create(config);
            log.info("RedissonClient 创建成功，连接地址: redis://{}:{}", redisHost, redisPort);
            return redissonClient;
        } catch (RedisConnectionException e) {
            log.error("RedissonClient 创建失败，Redis 连接异常: redis://{}:{}", redisHost, redisPort, e);
            throw new RuntimeException("Redis 连接失败，请检查 Redis 服务是否启动", e);
        }
    }
}