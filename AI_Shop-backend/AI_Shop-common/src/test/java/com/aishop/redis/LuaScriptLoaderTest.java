package com.aishop.redis;

import com.aishop.constants.Constants;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.data.redis.core.script.DefaultRedisScript;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 脚本从内联字符串挪到 resources/lua/ 之后，最大的风险是"文件没进包"或"文件名写错"——
 * 这类问题在单测里不查，就要等抢购请求打进来才炸。这里把每个脚本都真实加载一遍。
 * <p>脚本清单是扫 classpath 得来的，不是手写的：手写清单加脚本时会忘记同步
 * （{@code coupon_rush_sweep_dangling_v1.lua} 就漏过一次），漏掉的那个恰好就是没被覆盖的那个。
 */
class LuaScriptLoaderTest {

    private static List<String> allScripts() throws IOException {
        Resource[] found = new PathMatchingResourcePatternResolver()
                .getResources("classpath*:lua/*.lua");
        List<String> names = Arrays.stream(found)
                .map(Resource::getFilename)
                .filter(Objects::nonNull)
                .sorted()
                .toList();
        assertFalse(names.isEmpty(), "classpath 下没扫到任何 lua 脚本，打包配置可能有问题");
        return names;
    }

    @ParameterizedTest
    @MethodSource("allScripts")
    void loadsEveryScriptFromClasspath(String fileName) {
        DefaultRedisScript<Long> script = LuaScriptLoader.load(fileName, Long.class);
        assertNotNull(script.getScriptAsString());
        assertFalse(script.getScriptAsString().isBlank(), fileName + " 内容为空");
        // sha1 能算出来说明脚本文本已就位，Redis 侧才可能命中 EVALSHA
        assertEquals(40, script.getSha1().length());
    }

    @Test
    void allScriptsHaveDistinctSha1() throws IOException {
        // 两个文件 sha1 相同意味着内容重复，通常是复制粘贴时忘了改逻辑
        List<String> scripts = allScripts();
        long distinct = scripts.stream()
                .map(name -> LuaScriptLoader.load(name, Long.class).getSha1())
                .distinct()
                .count();
        assertEquals(scripts.size(), distinct);
    }

    @Test
    void sha1IsStableAcrossCallsOnSameInstance() {
        // EVALSHA 复用的前提：同一实例多次取 sha1 必须一致，脚本文本不能是动态拼的
        DefaultRedisScript<Long> script = LuaScriptLoader.load("rate_limit_v1.lua", Long.class);
        assertSame(script.getSha1(), script.getSha1());
    }

    @Test
    void scriptsDeclareKeysAndArgvContract() throws IOException {
        // 脚本正文若出现硬编码的业务 id，说明有人用 String.format 拼过参数，sha1 会随参数变化
        for (String name : allScripts()) {
            String body = LuaScriptLoader.load(name, Long.class).getScriptAsString();
            assertTrue(body.contains("ARGV[") || body.contains("KEYS["),
                    name + " 未通过 KEYS/ARGV 传参");
        }
    }

    @Test
    void hardcodedKeyPrefixesInScriptsMatchJavaConstants() throws IOException {
        // 脚本里键名是硬编码字符串，Java 侧是 Constants 拼的：改了 REDIS_KEY_PREFIX 编译器不会报错，
        // 脚本会安静地去读一批不存在的键——预占查不到、清理什么也摘不掉，全都不报错。
        Map<String, String> prefixByConstant = Map.of(
                "REDIS_KEY_RUSHING_STOCK", Constants.REDIS_KEY_RUSHING_STOCK,
                "REDIS_KEY_RUSHING_COUPON", Constants.REDIS_KEY_RUSHING_COUPON,
                "REDIS_KEY_RUSHING_USERID", Constants.REDIS_KEY_RUSHING_USERID);

        for (String name : allScripts()) {
            String body = LuaScriptLoader.load(name, Long.class).getScriptAsString();
            for (Matcher m = QUOTED_MALL_KEY.matcher(body); m.find(); ) {
                String literal = m.group(1);
                assertTrue(prefixByConstant.containsValue(literal),
                        name + " 里的键前缀 '" + literal + "' 不在 Constants 中，"
                                + "两侧已经不一致；应有之一: " + prefixByConstant.values());
            }
        }
    }

    /** 匹配脚本里形如 'mall:rushing:coupon:' 的键前缀字面量 */
    private static final Pattern QUOTED_MALL_KEY = Pattern.compile("'(mall:[^']*)'");

    @Test
    void everyScriptIsReferencedByProductionCode() throws IOException {
        // 只在 resources 里放脚本、Java 侧没人 load，等于死文件；反过来漏掉的脚本也在这条里暴露
        String sources = readAllJavaSources(Path.of("src/main/java"));
        for (String name : allScripts()) {
            assertTrue(sources.contains(name), name + " 没有被任何 Java 代码引用");
        }
    }

    private static String readAllJavaSources(Path root) throws IOException {
        try (var paths = Files.walk(root)) {
            StringBuilder sb = new StringBuilder();
            for (Path p : paths.filter(p -> p.toString().endsWith(".java")).toList()) {
                sb.append(Files.readString(p, StandardCharsets.UTF_8));
            }
            return sb.toString();
        }
    }

    @Test
    void failsFastWhenScriptMissing() {
        // 缺文件属于打包问题，要在类加载阶段就炸掉而不是返回空脚本
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> LuaScriptLoader.load("definitely_not_here_v9.lua", Long.class));
        assertTrue(e.getMessage().contains("definitely_not_here_v9.lua"));
    }
}
