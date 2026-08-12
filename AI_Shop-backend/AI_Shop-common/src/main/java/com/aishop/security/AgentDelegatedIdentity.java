package com.aishop.security;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.exception.HttpBusinessException;
import com.aishop.utils.StringTools;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * 解析 Agent 内部调用的委托用户身份（{@code X-Agent-User-Id}）。
 *
 * <p>内部接口靠 {@code X-Internal-Token} 证明"调用方是 Agent 系统"，但 token 证明不了
 * "这次查询代表哪个用户"。委托头由 Agent worker 从会话身份（系统信道）写入；body 里的
 * {@code userId} 是模型可见信道——模型输出或提示注入可以改写 body，改不了头。因此：
 *
 * <ul>
 *   <li>带用户数据的接口必须携带委托头，缺失即拒绝（fail-closed，防止漏带头被当成旧信任）；</li>
 *   <li>body 若也带 {@code userId}，必须与委托头一致，否则拒绝——模型把 body 身份换成本人
 *       之外的用户时，Java 侧能直接发现；</li>
 *   <li>按 id 直查的接口（getOrder 等）由控制器在查到数据后做归属校验。</li>
 * </ul>
 */
public final class AgentDelegatedIdentity {

    public static final String MISSING_MESSAGE = "缺少委托用户身份 X-Agent-User-Id";
    public static final String MISMATCH_MESSAGE = "委托身份与请求体身份不一致";

    private AgentDelegatedIdentity() {
    }

    /** 从当前线程的请求解析委托头；不存在时抛 401。 */
    public static String require() {
        ServletRequestAttributes attributes =
                (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attributes == null) {
            throw new HttpBusinessException(401, MISSING_MESSAGE);
        }
        return requireHeader(attributes.getRequest());
    }

    /**
     * 委托头 + body userId 一致性校验，返回权威身份。
     * body 未携带 userId 时仅要求委托头存在。
     */
    public static String requireAndMatch(Object bodyUserId) {
        String delegated = require();
        String claimed = bodyUserId == null ? "" : String.valueOf(bodyUserId).trim();
        if (!claimed.isEmpty() && !delegated.equals(claimed)) {
            throw new HttpBusinessException(403, MISMATCH_MESSAGE);
        }
        return delegated;
    }

    /** 归属校验：数据归属的 userId 必须等于委托身份。 */
    public static void requireOwner(String delegated, String ownerUserId) {
        if (StringTools.isEmpty(ownerUserId) || !delegated.equals(ownerUserId)) {
            throw new HttpBusinessException(403, "无权访问该数据");
        }
    }

    private static String requireHeader(HttpServletRequest request) {
        String delegated = request.getHeader(InternalApiHeaders.AGENT_USER_ID);
        if (StringTools.isEmpty(delegated)) {
            throw new HttpBusinessException(401, MISSING_MESSAGE);
        }
        return delegated;
    }
}
