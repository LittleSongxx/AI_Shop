const SUPPORT_STATUS = {
  QUEUED: { text: '排队中', type: 'warning' },
  ASSIGNED: { text: '已认领', type: 'primary' },
  ACTIVE: { text: '处理中', type: 'success' },
  RESOLVED: { text: '已解决', type: 'info' },
  CANCELLED: { text: '已取消', type: 'info' },
}

export const supportStatusText = (status) => SUPPORT_STATUS[status]?.text || status || '未知'

export const supportStatusType = (status) => SUPPORT_STATUS[status]?.type || ''

export const canClaimSupport = (status) => status === 'QUEUED'

export const canActivateSupport = (status) => status === 'ASSIGNED'

export const canHandleSupport = (status) => status === 'ASSIGNED' || status === 'ACTIVE'
