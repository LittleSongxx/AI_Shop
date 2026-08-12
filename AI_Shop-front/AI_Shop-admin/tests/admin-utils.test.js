import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/vue'
import BrandMark from '@/components/BrandMark.vue'
import {
  canCommend,
  canDelete,
  getDelistBlockReason,
} from '@/utils/productRules.js'
import { resolveDesktopPath } from '@/utils/device.js'
import {
  KNOWLEDGE_FILE_MAX_BYTES,
  validateKnowledgeFile,
} from '@/utils/knowledgeUpload.js'
import {
  canActivateSupport,
  canClaimSupport,
  canHandleSupport,
  supportStatusText,
} from '@/utils/supportStatus.js'
import {
  episodeVerdictLabel,
  formatAgentReply,
} from '@/utils/agentDisplay.js'
import {
  ADMIN_PERMISSION,
  hasAdminPermission,
  hasAnyAdminPermission,
  normalizeAdminPrincipal,
} from '@/utils/adminAccess.js'

describe('admin workflow helpers', () => {
  it('blocks unsafe product state transitions', () => {
    expect(getDelistBlockReason({ status: 1, commendType: 1 })).toContain('取消推荐')
    expect(canDelete({ status: 1, commendType: 0 })).toBe(false)
    expect(canCommend({ status: 0, commendType: 0 })).toBe(false)
  })

  it('keeps mobile and desktop routes aligned', () => {
    expect(resolveDesktopPath('/m/product/edit/p-1')).toBe('/product/updateProduct/p-1')
    expect(resolveDesktopPath('/m/more/agent')).toBe('/setting/agentMessage')
    expect(resolveDesktopPath('/m/more/agentQuality')).toBe('/setting/agentQuality')
    expect(resolveDesktopPath('/m/more/dataAnalyst')).toBe('/data/dataAnalyst')
    expect(resolveDesktopPath('/m/more/aiEvidence')).toBe('/data/aiEvidence')
  })

  it('uses server-issued administrator permissions for evidence actions', () => {
    const principal = normalizeAdminPrincipal({
      roles: ['DATA_ANALYST'],
      permissions: [ADMIN_PERMISSION.ANALYTICS_READ],
    })
    expect(hasAdminPermission(principal, ADMIN_PERMISSION.ANALYTICS_READ)).toBe(true)
    expect(hasAdminPermission(principal, ADMIN_PERMISSION.AI_PILOT)).toBe(false)
    expect(hasAnyAdminPermission(principal, [
      ADMIN_PERMISSION.AI_EVALUATE,
      ADMIN_PERMISSION.ANALYTICS_READ,
    ])).toBe(true)
    expect(normalizeAdminPrincipal(null)).toMatchObject({ roles: [], permissions: [] })
  })

  it('renders the brand marker as an accessible image', () => {
    const { getByRole } = render(BrandMark, { props: { variant: 'light' } })
    expect(getByRole('img', { name: '智选 SmartSelect' })).toBeTruthy()
  })

  it('validates knowledge document type and size before upload', () => {
    expect(validateKnowledgeFile({ name: 'policy.md', size: 1024 })).toBe('')
    expect(validateKnowledgeFile({ name: 'policy.exe', size: 1024 })).toContain('仅支持')
    expect(
      validateKnowledgeFile({
        name: 'policy.pdf',
        size: KNOWLEDGE_FILE_MAX_BYTES + 1,
      })
    ).toContain('10MB')
  })

  it('keeps human support actions aligned with the current state machine', () => {
    expect(supportStatusText('QUEUED')).toBe('排队中')
    expect(canClaimSupport('QUEUED')).toBe(true)
    expect(canHandleSupport('QUEUED')).toBe(false)
    expect(canActivateSupport('ASSIGNED')).toBe(true)
    expect(canHandleSupport('ASSIGNED')).toBe(true)
    expect(canHandleSupport('ACTIVE')).toBe(true)
    expect(canClaimSupport('RESOLVED')).toBe(false)
    expect(canHandleSupport('CANCELLED')).toBe(false)
  })

  it('renders trace business payloads and episode verdicts in readable Chinese', () => {
    const reply = formatAgentReply(JSON.stringify({
      type: 'PRODUCT_SEARCH_RESULT',
      intro: '**根据通勤需求推荐：**\n---',
      products: [{ productName: '降噪耳机', minPrice: 399, reason: '适合地铁通勤' }],
    }))
    expect(reply).toContain('根据通勤需求推荐')
    expect(reply).toContain('1. 降噪耳机，价格 ¥399，推荐理由：适合地铁通勤')
    expect(reply).not.toContain('PRODUCT_SEARCH_RESULT')
    expect(reply).not.toContain('**')
    expect(episodeVerdictLabel('NOT_ORDER_AFTERSALES')).toBe(
      '非订单售后场景，无需按售后数据集审核'
    )
  })
})
