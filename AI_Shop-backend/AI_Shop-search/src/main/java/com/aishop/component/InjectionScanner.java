package com.aishop.component;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.text.Normalizer;
import java.util.List;
import java.util.regex.Pattern;

/**
 * 知识库文档入库时的注入话术预扫描。
 *
 * <p>与 Python 侧 {@code channel_guard.py} 使用同一套规则语义：
 * 命中 BLOCKING 规则（明确的指令覆盖/提示泄露/角色劫持）直接拒绝入库；
 * 命中 SUSPICIOUS 规则的片段数量 &ge; 阈值时同样拒绝。
 * 这样"查询时检疫"就变成了双层防护的第二道门——入库时发现的问题
 * 在到达检索层之前就已被阻止，同时生成可观测信号。
 *
 * <p>调用方（{@code KnowledgeBaseServiceImpl#publish}）在文档正式写入 ES 之前调用
 * {@link #scan(String)}；发现污染时抛出 {@link ContaminatedDocumentException}，
 * 由上层返回 400 并写审计日志。
 */
@Component
@Slf4j
public class InjectionScanner {

    public static final class ContaminatedDocumentException extends RuntimeException {
        private final List<String> rules;

        public ContaminatedDocumentException(List<String> rules) {
            super("文档内容包含注入话术，已拒绝入库：" + rules);
            this.rules = rules;
        }

        public List<String> getRules() {
            return rules;
        }
    }

    // ── BLOCKING 规则 —— 命中即拒（购物知识库里没有正当用法） ─────────────────
    private static final List<Pattern> BLOCKING = List.of(
        // 指令覆盖（英文）
        Pattern.compile(
            "(?i)(ignore|disregard|forget)\\s+(all\\s+|any\\s+|the\\s+)?"
            + "(previous|prior|above|earlier|preceding)\\s+"
            + "(instruction|prompt|rule|direction)"),
        // 指令覆盖（中文）
        Pattern.compile(
            "(忽略|忽视|无视|忘记|忘掉|抛开)[^。；\\n]{0,4}"
            + "(前面|上面|之前|以上|先前|原有|所有|全部|你的|系统)[^。；\\n]{0,8}"
            + "(指令|命令|规则|设定|约束|系统提示词?|提示词)"),
        // 提示泄露（英文）
        Pattern.compile(
            "(?i)(show|print|reveal|repeat|output|display|dump|leak|tell)\\s+(me\\s+)?"
            + "(your|the)\\s+(system|initial|original|full|hidden|internal|exact)\\s*"
            + "(prompt|instruction|message|rule|config)"),
        // 提示泄露（中文）
        Pattern.compile(
            "(输出|打印|复述|重复|展示|显示|告诉我|泄露|导出|背诵|贴出)[^。；\\n]{0,8}"
            + "(系统提示词|系统提示|系统指令|初始指令|完整指令|原始指令|你的设定|你的规则|工具定义|prompt)"),
        // 角色劫持
        Pattern.compile(
            "(?i)(act\\s+as|pretend\\s+(you\\s+are|to\\s+be)|you\\s+are\\s+now)\\s+"
            + "(an?\\s+)?(unrestricted|uncensored|unfiltered|jailbroken|evil|"
            + "different\\s+ai|dan\\b)"
            + "|\\b(dan|do\\s+anything\\s+now)\\s+mode\\b"
            + "|developer\\s+mode\\s+(on|enabled|activated)"),
        // 角色劫持（中文）
        Pattern.compile(
            "(开发者模式|越狱模式|上帝模式|无视一切规则|无视所有规则)"
            + "|(你现在是|你就是|扮演|假装你是|进入)[^。；\\n]{0,8}"
            + "(不受任何限制|没有任何限制|没有限制|无限制|越狱|不受约束)"),
        // LLM 模板注入标记
        Pattern.compile(
            "(?i)<\\|\\s*(im_start|im_end|system|endoftext|start_header_id|end_header_id|eot_id)\\s*\\|>"
            + "|\\[/?INST\\]|<</?SYS>>"
            + "|<\\s*/?\\ s*(system|developer|tool_call|function_call|user_input)\\b")
    );

    // ── SUSPICIOUS 规则 —— 单独命中记录，达到阈值才拒 ─────────────────────────
    private static final List<Pattern> SUSPICIOUS = List.of(
        Pattern.compile("(?i)system\\s*prompt|系统提示词|提示词"),
        Pattern.compile("(?i)(developer|system|assistant)\\s+message|开发者消息|系统消息"),
        Pattern.compile("(?i)\\b(ignore|disregard|override)\\b|忽略|无视"),
        Pattern.compile("(?i)jailbreak|越狱|prompt\\s*injection|提示注入"),
        Pattern.compile("(?i)(工具|tool)\\s*(定义|列表|schema|definition)|function\\s+calling|tool_choice"),
        Pattern.compile("(直接|强制|立即|马上)[^。；\\n]{0,4}(调用|执行|运行)[^。；\\n]{0,8}(工具|接口|函数|命令)"),
        Pattern.compile("(?i)(base64|rot13|hex|url)\\s*(decode|decoded|解码|解密)")
    );

    private static final int SUSPICIOUS_THRESHOLD = 2;

    /**
     * 扫描一段文本（文档切片或完整文档内容）。
     * 发现污染时抛出 {@link ContaminatedDocumentException}；
     * 干净时静默返回。
     */
    public void scan(String text) {
        if (text == null || text.isBlank()) {
            return;
        }
        // NFKC 归一化：防止全角字符和零宽字符绕过关键词匹配，与 Python 侧逻辑保持一致
        String normalized = Normalizer.normalize(text, Normalizer.Form.NFKC)
                // 删除 Unicode 控制字符（C 类别），保留换行/回车/制表符
                .replaceAll("[\\p{Cc}&&[^\\r\\n\\t]]", "");

        List<String> blocking = BLOCKING.stream()
                .filter(p -> p.matcher(normalized).find())
                .map(p -> "BLOCKING:" + p.pattern().substring(0, Math.min(30, p.pattern().length())))
                .toList();

        if (!blocking.isEmpty()) {
            log.warn("injection_scanner_blocked document_content_length={} rules={}",
                    text.length(), blocking);
            throw new ContaminatedDocumentException(blocking);
        }

        List<String> suspicious = SUSPICIOUS.stream()
                .filter(p -> p.matcher(normalized).find())
                .map(p -> "SUSPICIOUS:" + p.pattern().substring(0, Math.min(30, p.pattern().length())))
                .toList();

        if (suspicious.size() >= SUSPICIOUS_THRESHOLD) {
            log.warn("injection_scanner_blocked_by_suspicious document_content_length={} rules={}",
                    text.length(), suspicious);
            throw new ContaminatedDocumentException(suspicious);
        }

        if (!suspicious.isEmpty()) {
            log.info("injection_scanner_suspicious document_content_length={} rules={}",
                    text.length(), suspicious);
        }
    }
}
