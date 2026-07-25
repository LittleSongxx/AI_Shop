package com.aishop.redis;

import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.script.DefaultRedisScript;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * 从 classpath 加载 Lua 脚本，包装成 {@link DefaultRedisScript}。
 * <p>脚本原本以字符串拼接的形式内联在各个 component 里，几十行的原子逻辑被 {@code " + "} 切碎之后
 * 很难 review，也没法用 Lua 工具链检查。挪到 {@code resources/lua/} 之后脚本是纯文本，改动能在 diff 里
 * 逐行看清。
 * <p>脚本文件名带版本号（如 {@code coupon_rush_reserve_v1.lua}）：线上一旦有节点还在跑旧版本，
 * 同名文件改内容会让两批节点的 sha1 不一致却指向同一语义，排查起来很痛苦。要改逻辑就加 v2 文件，
 * 老文件留着直到旧节点下线。
 * <p>返回的脚本实例必须由调用方以 {@code static final} 持有：{@link DefaultRedisScript} 的 sha1 是
 * 加锁懒加载的，复用同一实例才能一直命中 EVALSHA；每次新建实例等于每次重算 sha1、退回 EVAL 传全量脚本文本。
 * 同理，脚本里的可变参数一律走 KEYS/ARGV，不要在 Java 侧用 String.format 拼进脚本文本。
 */
public final class LuaScriptLoader {

    private static final String LUA_BASE_PATH = "lua/";

    private LuaScriptLoader() {
    }

    /**
     * 加载 {@code resources/lua/} 下的脚本。
     *
     * @param fileName   脚本文件名，含版本号后缀，例如 {@code coupon_rush_reserve_v1.lua}
     * @param resultType 脚本返回值类型，Lua 的 number 对应 {@link Long}，table 对应 {@link java.util.List}
     * @throws IllegalStateException 脚本文件缺失或读取失败时抛出。这属于打包问题，
     *                               宁可在类加载阶段就炸掉，也不要等到抢购请求打进来才发现脚本是空的
     */
    public static <T> DefaultRedisScript<T> load(String fileName, Class<T> resultType) {
        DefaultRedisScript<T> script = new DefaultRedisScript<>(readScript(fileName), resultType);
        // 提前触发 sha1 计算，脚本语法之外的问题（比如文件是空的）在启动时就能暴露
        script.getSha1();
        return script;
    }

    private static String readScript(String fileName) {
        String location = LUA_BASE_PATH + fileName;
        ClassPathResource resource = new ClassPathResource(location);
        if (!resource.exists()) {
            throw new IllegalStateException("Lua 脚本不存在: " + location);
        }
        try (InputStream in = resource.getInputStream()) {
            String content = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            if (content.isBlank()) {
                throw new IllegalStateException("Lua 脚本内容为空: " + location);
            }
            return content;
        } catch (IOException e) {
            throw new IllegalStateException("Lua 脚本读取失败: " + location, e);
        }
    }
}
