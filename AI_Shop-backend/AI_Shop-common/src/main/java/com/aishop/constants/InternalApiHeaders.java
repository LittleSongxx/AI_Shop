package com.aishop.constants;

public final class InternalApiHeaders {

    public static final String INTERNAL_TOKEN = "X-Internal-Token";
    public static final String INTERNAL_OPS_TOKEN = "X-Internal-Ops-Token";
    /**
     * Agent 内部调用的委托用户身份。由 Agent worker 从会话身份（系统信道）写入，
     * 与 body 里模型可见的 userId 分离：body 可被模型输出或提示注入改写，头不能。
     */
    public static final String AGENT_USER_ID = "X-Agent-User-Id";
    public static final String ADMIN_ID = "X-Admin-Id";
    public static final String ADMIN_ACCOUNT = "X-Admin-Account";
    public static final String ADMIN_ROLES = "X-Admin-Roles";
    public static final String ADMIN_PERMISSIONS = "X-Admin-Permissions";
    public static final String ADMIN_TIMESTAMP = "X-Admin-Timestamp";
    public static final String ADMIN_NONCE = "X-Admin-Nonce";
    public static final String ADMIN_BODY_SHA256 = "X-Admin-Body-SHA256";
    public static final String ADMIN_SIGNATURE = "X-Admin-Signature";
    public static final String ADMIN_KEY_ID = "X-Admin-Key-Id";
    public static final String TRACE_ID = "X-Trace-Id";
    public static final String TRACE_ID_MDC = "traceId";

    public static final String REMOTE_COMPENSATE_EXCHANGE = "REMOTE_COMPENSATE";
    public static final String REMOTE_STOCK_CHANGE_BATCH = "stock.changeBatch";
    public static final String REMOTE_COUPON_UNLOCK = "coupon.unlock";

    private InternalApiHeaders() {
    }
}
