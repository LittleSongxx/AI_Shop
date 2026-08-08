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
})
