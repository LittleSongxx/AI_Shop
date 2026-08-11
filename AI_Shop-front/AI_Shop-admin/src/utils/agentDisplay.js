const AGENT_LABELS = {
  supervisor: '协调主管',
  shopping_advisor: '智能导购专家',
  order_fulfillment_specialist: '订单履约专家',
  after_sales_policy_specialist: '售后政策专家',
  data_analyst: '经营分析专家',
  inventory_ops: '库存运营助手',
}

const INTENT_LABELS = {
  PRODUCT_CONSULT: '商品咨询',
  PRODUCT_SEARCH: '商品搜索与推荐',
  VISUAL_PRODUCT_SEARCH: '识图找同款或相似商品',
  QUERY_ORDER: '查询订单',
  REFUND: '退款与退货',
  CANCEL_ORDER: '取消订单',
  CONFIRM_RECEIPT: '确认收货',
  QUERY_LOGISTICS: '查询物流',
  QUERY_FULFILLMENT: '查询发货状态',
  QUERY_COUPON: '查询优惠券',
  PRODUCT_REVIEW: '提交评价',
  RECOMMENT: '追加评价',
  QUERY_COMMENT: '查询评价',
  COMPLAINT: '投诉处理',
  HUMAN_REQUEST: '请求人工客服',
  PAYMENT_ISSUE: '支付问题',
  DAMAGED_OR_WRONG_ITEM: '破损、错发或漏发',
  INVOICE: '发票问题',
  ADDRESS_CHANGE: '修改收货地址',
  REFUND_STATUS: '查询退款进度',
  AFTERSALES_UNKNOWN: '其他售后问题',
  CHAT: '一般咨询',
}

const REQUEST_MODE_LABELS = {
  INFORMATIONAL: '信息咨询',
  READ_QUERY: '个性化查询',
  ACTION_PROPOSAL: '操作提案',
  HUMAN_SUPPORT: '人工服务',
}

const BIZ_TYPE_LABELS = {
  agent: '普通文本回复',
  product_search: '商品推荐卡片',
  visual_product_search: '视觉商品推荐卡片',
  visual_subject_selection: '图片主体选择卡片',
  query_order: '订单查询卡片',
  order_selection: '订单选择卡片',
  action_confirm: '业务操作确认',
  support_case: '售后工单',
  support_case_list: '售后工单列表',
}

const SENTIMENT_LABELS = {
  POSITIVE: '积极',
  NEUTRAL: '中性',
  NEGATIVE: '消极',
  VERY_NEGATIVE: '强烈不满',
}

const URGENCY_LABELS = {
  LOW: '低',
  NORMAL: '普通',
  HIGH: '高',
  CRITICAL: '紧急',
}

const STATUS_LABELS = {
  RUNNING: '运行中',
  QUEUED: '排队中',
  STARTED: '已开始',
  OK: '正常',
  SUCCEEDED: '成功',
  SUCCESS: '成功',
  FAILED: '失败',
  ERROR: '异常',
  DEGRADED: '已降级',
  FALLBACK: '已降级处理',
  BLOCKED: '已拦截',
  REPAIRED: '已修正',
  HANDOFF: '已转人工',
  CANCELLED: '已取消',
  UNREVIEWED: '待审核',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
  NEW: '新发现',
  TRIAGED: '已分诊',
  LABELED: '已标注',
  FIXING: '修复中',
  REGRESSION_ADDED: '已加入回归',
  VERIFIED: '已验证',
  CLOSED: '已关闭',
  IGNORED: '已忽略',
  NOT_A_BUG: '非缺陷',
  NEEDS_CLARIFICATION: '需要澄清',
}

const OUTCOME_LABELS = {
  ok: '正常完成',
  completed: '正常完成',
  degraded: '降级完成',
  specialist_failed: '专家执行失败',
  llm_error: '模型调用异常',
  graph_error: '编排执行异常',
  cancelled: '用户已取消',
  handoff: '已转人工',
}

const EVENT_LABELS = {
  GRAPH_START: '开始执行 Agent 任务',
  GRAPH_END: '完成 Agent 任务',
  GUARD: '安全校验',
  MEMORY_READ: '读取会话记忆',
  INTENT_DECISION: '识别意图与请求方式',
  IMAGE_EVIDENCE: '核验图片证据',
  RAG_RETRIEVAL: '检索知识证据',
  RAG_QUERY_REJECTED: '知识检索请求被拒绝',
  SUPERVISOR_PLAN: '协调主管制定计划',
  SPECIALIST_CONTEXT: '准备专家上下文',
  SPECIALIST_STARTED: '专家开始执行',
  SPECIALIST_TOOL: '专家查询业务工具',
  SPECIALIST_ARTIFACT: '专家提交事实产物',
  ARTIFACT_VALIDATION: '校验专家产物',
  FANOUT_TIMEOUT: '并行专家执行超时',
  FANOUT_DEGRADED: '并行协作降级',
  SUPERVISOR_SYNTHESIS: '协调主管汇总答复',
  ACTION_POLICY_DECISION: '评估业务操作提案',
  SHOPPING_MISSION_UPDATE: '更新导购需求任务',
  SHOPPING_CLARIFICATION_DECISION: '选择高价值追问',
  PRODUCT_FACTS_VERIFIED: '核验商品决策事实',
  OFFER_SNAPSHOT_CREATED: '生成实时单 SKU 报价',
  OFFER_SNAPSHOT_UNAVAILABLE: '实时商品报价不可用',
  RANKING_POLICY_DECISION: '执行用户利益排序',
  COMMERCE_OUTCOME_RECORDED: '记录长期经营结果',
  AFTER_SALES_ELIGIBILITY_DECISION: '核验售后资格',
  RESPONSE_VERIFIER: '核验最终答复',
  MEMORY_WRITE: '更新长期记忆',
  NODE_TRANSITION: '内部节点流转',
  LLM_CALL: '调用语言模型',
  TOOL_CALL: '调用业务工具',
  MQ_PUBLISH: '提交异步任务',
  MQ_RECEIVE: '异步任务开始处理',
  DATA_ANALYST_PLAN: '经营分析计划',
  DATA_ANALYST_BRANCH_ERROR: '指标分支执行失败',
  DATA_ANALYST_SQL_GUARD: '经营分析 SQL 安全校验',
  DATA_ANALYST_EXPLAIN: '检查经营分析执行计划',
  DATA_ANALYST_QUERY: '执行经营分析只读查询',
  DATA_ANALYST_RESULT: '生成经营分析结论',
  DATA_ANALYST_TIMEOUT: '经营分析超过整体预算',
  INVENTORY_OPS_PLAN: '制定库存运营分析计划',
  INVENTORY_OPS_RESULT: '生成库存运营人工建议',
  SQL_GUARD: 'SQL 安全校验',
  SQL_EXPLAIN: '查询计划检查',
  SQL_EXECUTION: '执行只读分析查询',
  IMAGE_ASSET_VERIFIED: '图片资产已验证',
  VISUAL_SUBJECTS_GROUNDED: '商品主体已定位',
  VISUAL_SUBJECT_SELECTION_REQUIRED: '等待用户选择商品主体',
  VISUAL_SUBJECT_SELECTED: '用户已选择商品主体',
  VISUAL_QUERY_EMBEDDED: '视觉查询已向量化',
  VISUAL_CANDIDATES_RECALLED: '视觉候选已召回',
  VISUAL_RESULTS_FUSED: '多路检索结果已融合',
  VISUAL_CANDIDATES_RERANKED: '视觉候选已精排',
  VISUAL_PRODUCT_SNAPSHOTS_VERIFIED: '商品权威快照已校验',
  VISUAL_NO_CONFIDENT_MATCH: '未发现可靠的视觉匹配',
  VISUAL_CAPABILITY_DEGRADED: '视觉能力已降级',
  VISUAL_INDEX_UPDATED: '视觉商品索引已更新',
}

const NODE_LABELS = {
  entry: '请求入口',
  build_context: '理解请求并构建上下文',
  order_reference: '核验订单归属与引用',
  multi_agent_plan: '制定多智能体计划',
  supervisor_plan: '制定多智能体计划',
  specialist_runner: '专家执行器',
  artifact_validator: '专家产物校验器',
  multi_agent_synthesis: '汇总专家结果',
  supervisor_synthesis: '汇总专家结果',
  action_executor: '操作提案执行器',
  agent_loop: '单智能体推理',
  tools: '业务工具执行',
  finalize: '答复校验与落库',
  post_turn: '会话记忆更新',
  visual_search: '视觉商品检索',
  visual_index: '视觉商品索引',
  shopping_mission: '导购需求任务',
  shopping_decision: '可信商品决策',
  commerce_outcome_ledger: '长期经营结果账本',
  after_sales_policy_specialist: '售后规则核验',
  data_analyst_plan: '经营分析规划',
  data_analyst_explain: '分析查询计划',
  data_analyst_query: '只读经营查询',
  data_analyst_result: '经营分析结论',
  inventory_ops_plan: '库存运营规划',
  inventory_ops_result: '库存运营建议',
  cleanup: '运行清理',
}

const TOOL_LABELS = {
  SEARCH_PRODUCTS: '搜索商品',
  SEARCH_PRODUCTS_BY_IMAGE: '按图片搜索商品',
  GET_PRODUCT_DETAIL: '查询商品详情',
  COMPARE_PRODUCTS: '比较商品',
  QUERY_ORDERS: '查询订单',
  QUERY_LOGISTICS: '查询物流',
  QUERY_COMMENT: '查询评价',
  QUERY_REFUND_STATUS: '查询退款进度',
  CHECK_AFTER_SALES_ELIGIBILITY: '核验售后资格',
  QUERY_USER_COUPONS: '查询用户优惠券',
  QUERY_SUPPORT_CASES: '查询售后工单',
  SEARCH_KNOWLEDGE: '检索知识库',
  PROPOSE_REFUND: '创建退款确认提案',
  PROPOSE_CANCEL_ORDER: '创建取消订单确认提案',
  PROPOSE_CONFIRM_RECEIPT: '创建确认收货提案',
  PROPOSE_PRODUCT_REVIEW: '创建评价确认提案',
  PROPOSE_RECOMMENT: '创建追评确认提案',
  PROPOSE_CREATE_SUPPORT_CASE: '创建售后工单确认提案',
}

const REASON_LABELS = {
  DYNAMIC_FACT_WITHOUT_TOOL: '答复包含未经业务工具核验的动态事实',
  POLICY_WITHOUT_CITATION: '政策结论缺少可引用的知识证据',
  POLICY_EVIDENCE_MISSING: '售后政策证据不足',
  POLICY_EVIDENCE_INSUFFICIENT: '政策证据不足，禁止创建操作提案',
  POLICY_TEXT_EVIDENCE_MISSING: '资格已核验，但缺少政策文本引用',
  ELIGIBILITY_EVIDENCE_MISSING: '缺少具体订单的售后资格决策',
  AFTER_SALES_NOT_ELIGIBLE: '资格引擎未判定为可申请，禁止创建退款提案',
  POLICY_UNAVAILABLE: '没有匹配的已发布售后规则',
  OVER_BUDGET: '估算到手价超过用户预算',
  BELOW_BUDGET_RANGE: '估算到手价低于用户指定价格区间',
  BRAND_REQUIRED: '商品不符合用户明确品牌条件',
  BRAND_EXCLUDED: '商品属于用户明确排除的品牌',
  NOT_PURCHASABLE: '商品已下架或没有可购买库存',
  QUOTE_MISSING: '缺少可验证的单 SKU 报价',
  ORDER_EVIDENCE_INSUFFICIENT: '订单证据不足，禁止创建操作提案',
  SPECIALIST_TIMEOUT: '专家执行超时',
  SPECIALIST_EMPTY_ARTIFACT: '专家没有返回可用事实',
  SPECIALIST_ROUND_LIMIT: '专家达到最大推理轮次',
  SPECIALIST_TOOL_PROTOCOL_REJECTED: '已拦截模型返回的未解析工具协议',
  MODEL_SUMMARY_PROTOCOL_SANITIZED: '模型摘要协议残片已清理，已改用可信工具事实',
  SPECIALIST_ACTION_DROPPED: '已移除专家越权生成的操作',
  SPECIALIST_ACTION_CARD_DROPPED: '已移除专家越权生成的确认卡片',
  UNVERIFIED_FACTS_DROPPED: '已移除没有工具证据的事实',
  UNTRUSTED_EVIDENCE_DROPPED: '已移除无法信任的证据',
  UNVERIFIED_ASSISTANT_CARD_DROPPED: '已移除未经对应工具核验的业务卡片',
  ACTION_EXECUTOR_FAILED: '操作提案执行器异常',
  ACTION_REJECTED: '操作提案被业务规则拒绝',
  VERIFIED_ACTION_ARGS_MISSING: '缺少服务端核验后的操作参数',
  RAG_EMPTY_QUERY: '知识检索词为空',
  RAG_DUPLICATE_QUERY: '知识检索词重复',
  RAG_RETRIEVAL_LIMIT: '知识检索次数已达上限',
  RAG_NOT_ALLOWED: '当前阶段不允许再次检索知识',
  TOOL_DUPLICATE_DENIED: '已阻止专家重复调用工具',
  TOOL_SCOPE_DENIED: '已阻止专家调用任务范围外的工具',
  SPECIALIST_TASK_TOOL_SCOPE_INVALID: '专家任务的工具权限范围无效',
  SPECIALIST_TASK_REQUIRED_TOOL_INVALID: '专家任务包含未授权的必查工具',
  BUSINESS_REJECTED: '当前业务状态不支持该项查询或操作',
  NOT_FOUND: '未查询到对应业务记录',
  TOOL_ERROR: '业务工具调用异常',
  VISUAL_CONTEXT_REQUIRED: '当前请求缺少服务端验证过的图片上下文',
  VISUAL_CONTEXT_INVALID: '图片上下文未通过服务端校验',
  VISUAL_SEARCH_DISABLED: '视觉商品搜索当前未启用',
  VISUAL_IMAGE_INVALID: '图片内容或格式未通过校验',
  VISUAL_SELECTION_CONTEXT_MISSING: '缺少创建商品主体选择所需的消息上下文',
  VISUAL_GROUNDING_CIRCUIT_OPEN: '主体定位服务熔断中，已改用整张图片',
  VISUAL_GROUNDING_TEMPORARILY_UNAVAILABLE: '主体定位服务暂时不可用，已改用整张图片',
  VISUAL_EMBEDDING_CIRCUIT_OPEN: '视觉向量服务熔断中，已改用图片内容理解',
  VISUAL_EMBEDDING_TEMPORARILY_UNAVAILABLE: '视觉向量服务暂时不可用，已改用图片内容理解',
  VISUAL_RERANK_CIRCUIT_OPEN: '视觉精排服务熔断中，已保留确定性融合排序',
  VISUAL_RERANK_TEMPORARILY_UNAVAILABLE: '视觉精排服务暂时不可用，已保留确定性融合排序',
  VISUAL_INDEX_UNAVAILABLE: '视觉商品索引暂时不可用',
  VISUAL_RERANK_IMAGES_UNAVAILABLE: '候选商品图片不足，已保留融合排序',
  NO_CONFIDENT_MATCH: '没有候选达到离线标定的视觉相似度门槛',
  DATA_ANALYST_BRANCH_FAILED: '经营分析指标分支失败',
  DATA_ANALYST_BRANCH_COUNT_INVALID: '经营分析分支数量不合法',
  DATA_ANALYST_BRANCH_DUPLICATE: '经营分析分支编号重复',
  DATA_ANALYST_PLAN_PARSE_FAILED: '经营分析计划解析失败',
  DATA_ANALYST_PLAN_TIMEOUT: '经营分析计划生成超时',
  DATA_ANALYST_SQL_PARSE_FAILED: '经营分析 SQL 解析失败',
  DATA_ANALYST_SQL_TIMEOUT: '经营分析 SQL 生成超时',
  DATA_ANALYST_REQUEST_TIMEOUT: '经营分析超过整体时间预算',
  DATA_ANALYST_MODEL_UNAVAILABLE: '经营分析模型暂时不可用',
  SQL_DRAFT_FAILED: '经营分析 SQL 草稿生成失败',
  QUERY_TIMEOUT: '只读查询超过时间预算',
  DATABASE_UNAVAILABLE: '只读分析数据库暂时不可用',
  EXPLAIN_SKIPPED_VIEW_PRIVILEGE: '受视图权限边界影响，已跳过执行计划检查',
  INVENTORY_FORECAST_PERSIST_DEGRADED: '库存预测审计留痕暂时降级',
  INVENTORY_LOOKBACK_FIXED_TO_28_DAYS: '库存预测已固定使用 28 天口径',
  INVENTORY_SQL_GUARD_FAILED: '库存分析查询未通过安全校验',
}

const EPISODE_VERDICT_LABELS = {
  COMPLETE: '事实完整，可进入人工审核',
  NOT_ORDER_AFTERSALES: '非订单售后场景，无需按售后数据集审核',
  VERIFIER_FAILED: '最终答复核验未通过',
  RUN_NOT_TERMINAL: '运行尚未结束',
  SUPPORT_CASE_RESOLVED: '售后工单已解决，结果完整',
  SUPPORT_CASE_OPEN: '售后工单仍待处理',
  SUPPORT_CASE_WITHOUT_RESOLUTION: '售后工单缺少解决结果',
  AWAITING_CONFIRMATION: '等待用户确认业务操作',
  OUTCOME_UNKNOWN: '业务操作结果尚不明确',
  CANCEL_CONFIRMED: '取消订单已确认完成',
  ACTION_FAILED_WITH_KNOWN_OUTCOME: '业务操作失败，但结果已明确记录',
  INCOMPLETE_ACTION_FACTS: '业务操作事实不完整',
}

const labelOf = (labels, value, fallback = '—') => {
  const code = String(value || '').trim()
  return code ? labels[code] || code : fallback
}

export const agentLabel = (value) => labelOf(AGENT_LABELS, value)
export const intentLabel = (value) => labelOf(INTENT_LABELS, value)
export const requestModeLabel = (value) => labelOf(REQUEST_MODE_LABELS, value)
export const bizTypeLabel = (value) => labelOf(BIZ_TYPE_LABELS, value)
export const sentimentLabel = (value) => labelOf(SENTIMENT_LABELS, value)
export const urgencyLabel = (value) => labelOf(URGENCY_LABELS, value)
export const statusLabel = (value) => labelOf(STATUS_LABELS, value)
export const outcomeLabel = (value) => labelOf(OUTCOME_LABELS, value)
export const eventLabel = (value) => labelOf(EVENT_LABELS, value)
export const nodeLabel = (value) => labelOf(NODE_LABELS, value)
export const toolLabel = (value) => labelOf(TOOL_LABELS, value, '无业务操作')
export const episodeVerdictLabel = (value) => labelOf(EPISODE_VERDICT_LABELS, value)

const productPriceText = (product) => {
  const min = Number(product?.minPrice)
  const max = Number(product?.maxPrice)
  if (!Number.isFinite(min)) return ''
  if (Number.isFinite(max) && max > min) return `，价格 ¥${min}-${max}`
  return `，价格 ¥${min}`
}

const plainTraceText = (value) => String(value || '')
  .replace(/\*\*([^*]+)\*\*/g, '$1')
  .replace(/^\s*---+\s*$/gm, '')
  .replace(/^#{1,6}\s+/gm, '')
  .replace(/\n{3,}/g, '\n\n')
  .trim()

export const formatAgentReply = (value, fallback = '尚未生成最终回复') => {
  const text = String(value || '').trim()
  if (!text) return fallback
  let payload
  try {
    payload = JSON.parse(text)
  } catch {
    return plainTraceText(text)
  }
  if (!payload || Array.isArray(payload) || typeof payload !== 'object') return text

  const intro = plainTraceText(
    payload.intro || payload.message || payload.summary || payload.answer || ''
  )
  if (payload.type === 'PRODUCT_SEARCH_RESULT') {
    const products = Array.isArray(payload.products) ? payload.products : []
    const lines = products.map((product, index) => {
      const name = product?.productName || product?.name || `商品 ${index + 1}`
      const reason = product?.reason ? `，推荐理由：${product.reason}` : ''
      return `${index + 1}. ${name}${productPriceText(product)}${reason}`
    })
    return [intro, lines.length ? `商品结果：\n${lines.join('\n')}` : '未返回匹配商品。']
      .filter(Boolean)
      .join('\n\n')
  }
  if (payload.type === 'ORDER_SELECTION') {
    const candidates = Array.isArray(payload.candidates) ? payload.candidates : []
    const lines = candidates.map((candidate, index) => (
      `${index + 1}. ${candidate?.productName || candidate?.orderId || '订单候选'}`
    ))
    return [intro || '需要用户选择具体订单', lines.join('\n')].filter(Boolean).join('\n')
  }
  if (payload.type === 'ACTION_CONFIRM') {
    const action = intentLabel(payload.actionType)
    const target = payload.orderId ? `订单 ${payload.orderId}` : ''
    return [intro || payload.title || '等待用户确认业务操作', `${action}${target ? ` · ${target}` : ''}`, payload.confirmText]
      .filter(Boolean)
      .join('\n')
  }
  return intro || fallback
}

export const reasonLabel = (value) => {
  const code = String(value || '').trim()
  if (!code) return '—'
  const [base, detail] = code.split(':', 2)
  const translated = REASON_LABELS[base] || code
  return detail && translated !== code ? `${translated}（${toolLabel(detail)}）` : translated
}

export const rawCode = (value) => String(value || '').trim() || '—'
