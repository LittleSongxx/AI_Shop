const isObject = (value) => value && typeof value === 'object' && !Array.isArray(value)

export const normalizeHandoffContext = (value) => {
  if (!isObject(value) || value.schemaVersion !== 'aishop-support-handoff/v1') return null
  const triage = isObject(value.triage) ? value.triage : {}
  const recentConversation = Array.isArray(value.recentConversation)
    ? value.recentConversation
        .filter(isObject)
        .slice(-6)
        .map((item) => ({
          role: String(item.role || 'UNKNOWN'),
          content: String(item.content || '').slice(0, 200),
        }))
        .filter((item) => item.content)
    : []
  const authoritativeOrders = Array.isArray(value.authoritativeOrders)
    ? value.authoritativeOrders
        .filter(
          (item) =>
            isObject(item) &&
            item.authority === 'JAVA_ORDER_SERVICE' &&
            item.ownershipVerified === true
        )
        .slice(0, 3)
    : []
  return {
    schemaVersion: value.schemaVersion,
    request: String(value.request || ''),
    recentConversation,
    triage: {
      intent: String(triage.intent || 'UNKNOWN'),
      confidence: Number(triage.confidence || 0),
      sentiment: String(triage.sentiment || 'NEUTRAL'),
      urgency: String(triage.urgency || 'NORMAL'),
      riskLevel: String(triage.riskLevel || 'LOW'),
    },
    handoffReason: String(value.handoffReason || 'AI_HANDOFF'),
    unverifiedHints: isObject(value.unverifiedHints) ? value.unverifiedHints : {},
    authoritativeOrders,
  }
}

export const formatHandoffHints = (value) => {
  if (!isObject(value) || !Object.keys(value).length) return '无'
  return JSON.stringify(value, null, 2)
}

export const handoffOrderProducts = (order) => {
  if (!Array.isArray(order?.items) || !order.items.length) return '无商品明细'
  return order.items
    .slice(0, 5)
    .map((item) => String(item?.productName || item?.productId || '未知商品'))
    .join('、')
}
