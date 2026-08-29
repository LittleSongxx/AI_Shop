export const AGENT_ACTION_STATUS = {
  PENDING: 0,
  CONFIRMED: 1,
  CANCELLED: 2,
  EXECUTING: 3,
  FAILED: 4,
  EXPIRED: 5,
  INCONCLUSIVE: 6,
  MANUAL_REVIEW: 7
} as const;

export const normalizeAgentActionStatus = (value: unknown): number => {
  if (typeof value === 'string') {
    const named: Record<string, number> = {
      PENDING: AGENT_ACTION_STATUS.PENDING,
      CONFIRMED: AGENT_ACTION_STATUS.CONFIRMED,
      CANCELLED: AGENT_ACTION_STATUS.CANCELLED,
      EXECUTING: AGENT_ACTION_STATUS.EXECUTING,
      FAILED: AGENT_ACTION_STATUS.FAILED,
      EXPIRED: AGENT_ACTION_STATUS.EXPIRED,
      INCONCLUSIVE: AGENT_ACTION_STATUS.INCONCLUSIVE,
      MANUAL_REVIEW: AGENT_ACTION_STATUS.MANUAL_REVIEW
    };
    const byName = named[value.trim().toUpperCase()];
    if (byName != null) return byName;
  }
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 && parsed <= 7
    ? parsed
    : AGENT_ACTION_STATUS.PENDING;
};

export const agentActionStatusClass = (value: unknown): string => {
  switch (normalizeAgentActionStatus(value)) {
    case AGENT_ACTION_STATUS.CONFIRMED:
      return 'is-confirmed';
    case AGENT_ACTION_STATUS.CANCELLED:
      return 'is-cancelled';
    case AGENT_ACTION_STATUS.EXECUTING:
    case AGENT_ACTION_STATUS.INCONCLUSIVE:
      return 'is-executing';
    case AGENT_ACTION_STATUS.MANUAL_REVIEW:
      return 'is-manual-review';
    case AGENT_ACTION_STATUS.FAILED:
      return 'is-failed';
    case AGENT_ACTION_STATUS.EXPIRED:
      return 'is-expired';
    default:
      return 'is-pending';
  }
};

export const agentActionStatusLabel = (value: unknown): string => {
  switch (normalizeAgentActionStatus(value)) {
    case AGENT_ACTION_STATUS.CONFIRMED:
      return '已确认执行';
    case AGENT_ACTION_STATUS.CANCELLED:
      return '已取消';
    case AGENT_ACTION_STATUS.EXECUTING:
      return '执行中，请勿重复操作';
    case AGENT_ACTION_STATUS.INCONCLUSIVE:
      return '执行结果核对中，请勿重复操作';
    case AGENT_ACTION_STATUS.MANUAL_REVIEW:
      return '自动核对已到边界，等待人工复核';
    case AGENT_ACTION_STATUS.FAILED:
      return '执行失败，请重新发起';
    case AGENT_ACTION_STATUS.EXPIRED:
      return '已过期，请重新发起';
    default:
      return '';
  }
};
