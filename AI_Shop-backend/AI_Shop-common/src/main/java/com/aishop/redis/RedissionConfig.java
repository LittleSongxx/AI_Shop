package com.aishop.redis;

import lombok.extern.slf4j.Slf4j;
import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.client.RedisConnectionException;
import org.redisson.config.Config;
import org.redisson.config.SingleServerConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

@Configuration
@Slf4j
public class RedissionConfig {

    private final String redisHost;
    private final int redisPort;
    private final String redisUsername;
    private final String redisPassword;
    private final int redisDatabase;
    private final boolean redisSslEnabled;

    public RedissionConfig(
            @Value("${spring.data.redis.host:127.0.0.1}") String redisHost,
            @Value("${spring.data.redis.port:6380}") int redisPort,
            @Value("${spring.data.redis.username:}") String redisUsername,
            @Value("${spring.data.redis.password:}") String redisPassword,
            @Value("${spring.data.redis.database:0}") int redisDatabase,
            @Value("${spring.data.redis.ssl.enabled:false}") boolean redisSslEnabled) {
        this.redisHost = redisHost;
        this.redisPort = redisPort;
        this.redisUsername = redisUsername;
        this.redisPassword = redisPassword;
        this.redisDatabase = redisDatabase;
        this.redisSslEnabled = redisSslEnabled;
    }

    @Bean(value = "redissonClient", destroyMethod = "shutdown")
    public RedissonClient redissonClient() {
        try {
            RedissonClient redissonClient = Redisson.create(buildConfig());
            log.info("RedissonClient 创建成功，连接地址: {}://{}:{}, database={}",
                    redisSslEnabled ? "rediss" : "redis", redisHost, redisPort, redisDatabase);
            return redissonClient;
        } catch (RedisConnectionException e) {
            log.error("RedissonClient 创建失败，Redis 连接异常: {}://{}:{}",
                    redisSslEnabled ? "rediss" : "redis", redisHost, redisPort, e);
            throw new RuntimeException("Redis 连接失败，请检查 Redis 服务是否启动", e);
        }
    }

    Config buildConfig() {
        Config config = new Config();
        config.useSingleServer()
                .setAddress((redisSslEnabled ? "rediss://" : "redis://") + redisHost + ":" + redisPort)
                .setDatabase(redisDatabase);
        if (StringUtils.hasText(redisUsername)) {
            config.setUsername(redisUsername);
        }
        if (StringUtils.hasText(redisPassword)) {
            config.setPassword(redisPassword);
        }
        return config;
    }
}
