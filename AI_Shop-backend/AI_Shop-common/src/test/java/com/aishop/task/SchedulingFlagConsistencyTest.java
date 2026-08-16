package com.aishop.task;

import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 定时任务开关与各服务实际 schema 的一致性审计。
 * <p>起因是两个真实踩过的坑：
 * <ul>
 *   <li>{@code MqCompensationAutoReplayTask} 的开关是 {@code matchIfMissing = true}，
 *       任何服务一打开 {@code app.common-scheduling.enabled} 它就会跟着注册。而
 *       {@code mq_compensation_log} 表只在部分库里有，没这张表的服务会每分钟查一张
 *       不存在的表并打 error——不影响功能，但会把真正的错误日志淹掉。</li>
 *   <li>反过来，{@code user} 长期没声明 {@code app.common-scheduling.enabled}，
 *       它的 {@code SignReconcileTask} 从来没注册过。开关缺失和显式关闭在行为上一样，
 *       但一个是疏漏、一个是决定，看 yml 分不出来。</li>
 * </ul>
 * <p>所以这里要求每个服务都显式表态，并且表态要和自己的建表脚本对得上。
 * 这条约束跨模块，只能在测试里以文件为准来查——放在任何单个服务里都看不全。
 */
class SchedulingFlagConsistencyTest {

    private static final String SCHEDULING_FLAG = "app.common-scheduling.enabled";
    private static final String AUTO_REPLAY_FLAG = "mq.compensation.auto-replay-enabled";
    private static final String OUTBOX_DISPATCH_FLAG = "mq.outbox.dispatch-enabled";
    private static final String COMPENSATION_TABLE = "mq_compensation_log";
    private static final String OUTBOX_TABLE = "local_message_outbox";

    /** 一个业务服务：application.yml 与建表脚本 */
    private record Service(String name, Path config, Path schema) {
    }

    /** 测试的工作目录是 AI_Shop-common 模块根，兄弟模块在上一级 */
    private static List<Service> services() throws IOException {
        Path backend = Path.of("..").toRealPath();
        List<Service> found = new ArrayList<>();
        try (Stream<Path> dirs = Files.list(backend)) {
            for (Path module : dirs.filter(Files::isDirectory).sorted().toList()) {
                String dirName = module.getFileName().toString();
                if (!dirName.startsWith("AI_Shop-") || dirName.equals("AI_Shop-common")) {
                    continue;
                }
                // 有的模块是 <module>/app/src/...，有的是 <module>/src/...
                for (Path base : List.of(module.resolve("app"), module)) {
                    Path config = base.resolve("src/main/resources/application.yml");
                    if (Files.exists(config)) {
                        found.add(new Service(
                                dirName.substring("AI_Shop-".length()),
                                config,
                                base.resolve("src/main/resources/db/migration/R__current_schema.sql")));
                        break;
                    }
                }
            }
        }
        // 目录结构变了就该在这里炸掉，而不是扫到 0 个服务然后安静通过
        assertFalse(found.isEmpty(), "没扫到任何服务的 application.yml，仓库结构可能变了");
        return found;
    }

    private static Object flatGet(Path yml, String dottedKey) throws IOException {
        Map<String, Object> root;
        try (InputStream in = Files.newInputStream(yml)) {
            root = new Yaml().load(in);
        }
        Object node = root == null ? null : root;
        for (String part : dottedKey.split("\\.")) {
            if (!(node instanceof Map<?, ?> map)) {
                return null;
            }
            node = map.get(part);
        }
        return node;
    }

    private static boolean hasTable(Path schema, String table) throws IOException {
        if (!Files.exists(schema)) {
            return false;
        }
        return Files.readString(schema, StandardCharsets.UTF_8)
                .toLowerCase()
                .contains(table);
    }

    private static boolean hasCompensationTable(Path schema) throws IOException {
        return hasTable(schema, COMPENSATION_TABLE);
    }

    @Test
    void everyServiceStatesItsSchedulingFlagExplicitly() throws IOException {
        Map<String, Object> missing = new LinkedHashMap<>();
        for (Service s : services()) {
            Object value = flatGet(s.config(), SCHEDULING_FLAG);
            if (value == null) {
                missing.put(s.name(), null);
            }
        }
        // gateway 之类没有定时任务的服务也要写：写出来才能区分"不需要"和"忘了"
        assertEquals(Map.of(), missing,
                "以下服务没有声明 " + SCHEDULING_FLAG + "，无法区分是有意关闭还是漏配: " + missing.keySet());
    }

    @Test
    void schedulingEnabledServicesWithoutTheTableMustDisableAutoReplay() throws IOException {
        List<String> offenders = new ArrayList<>();
        for (Service s : services()) {
            if (!Boolean.TRUE.equals(flatGet(s.config(), SCHEDULING_FLAG))) {
                continue;
            }
            if (hasCompensationTable(s.schema())) {
                continue;
            }
            // 没有 mq_compensation_log 却没显式关掉：matchIfMissing = true 会让任务照样注册
            if (!Boolean.FALSE.equals(flatGet(s.config(), AUTO_REPLAY_FLAG))) {
                offenders.add(s.name());
            }
        }
        assertTrue(offenders.isEmpty(),
                "以下服务打开了定时任务但库里没有 " + COMPENSATION_TABLE
                        + "，必须显式设置 " + AUTO_REPLAY_FLAG + "=false: " + offenders);
    }

    @Test
    void autoReplayEnabledServicesActuallyHaveTheTable() throws IOException {
        List<String> offenders = new ArrayList<>();
        for (Service s : services()) {
            if (!Boolean.TRUE.equals(flatGet(s.config(), AUTO_REPLAY_FLAG))) {
                continue;
            }
            if (!hasCompensationTable(s.schema())) {
                offenders.add(s.name());
            }
        }
        // 反向约束：显式打开了却没建表，说明补偿日志根本没落库，重放任务是空转
        assertTrue(offenders.isEmpty(),
                "以下服务打开了 " + AUTO_REPLAY_FLAG + " 但库里没有 " + COMPENSATION_TABLE + ": " + offenders);
    }

    @Test
    void outboxDispatchEnabledServicesActuallyHaveTheTable() throws IOException {
        List<String> offenders = new ArrayList<>();
        for (Service s : services()) {
            if (!Boolean.TRUE.equals(flatGet(s.config(), OUTBOX_DISPATCH_FLAG))) {
                continue;
            }
            if (!hasTable(s.schema(), OUTBOX_TABLE)) {
                offenders.add(s.name());
            }
        }
        assertTrue(offenders.isEmpty(),
                "以下服务打开了 " + OUTBOX_DISPATCH_FLAG + " 但库里没有 "
                        + OUTBOX_TABLE + ": " + offenders);
    }
}
