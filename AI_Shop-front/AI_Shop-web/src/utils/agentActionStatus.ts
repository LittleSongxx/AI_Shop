export const AGENT_ACTION_STATUS = {
  PENDING: 0,
  CONFIRMED: 1,
  CANCELLED: 2,
  EXECUTING: 3,
  FAILED: 4,
  EXPIRED: 5
} as const;

export const normalizeAgentActionStatus = (value: unknown): number => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 && parsed <= 5
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
      return 'is-executing';
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
    case AGENT_ACTION_STATUS.FAILED:
      return '执行失败，请重新发起';
    case AGENT_ACTION_STATUS.EXPIRED:
      return '已过期，请重新发起';
    default:
      return '';
  }
};
