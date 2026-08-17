import { describe, expect, it } from 'vitest'

import {
  formatHandoffHints,
  handoffOrderProducts,
  normalizeHandoffContext,
} from '../src/utils/handoffContext.js'

describe('handoff context', () => {
  it('shows only Java-owned and ownership-verified order facts', () => {
    const context = normalizeHandoffContext({
      schemaVersion: 'aishop-support-handoff/v1',
      request: '商品破损',
      authoritativeOrders: [
        {
          authority: 'JAVA_ORDER_SERVICE',
          ownershipVerified: true,
          orderId: 'owned',
          items: [{ productName: '耳机' }],
        },
        {
          authority: 'MODEL',
          ownershipVerified: true,
          orderId: 'model-only',
        },
        {
          authority: 'JAVA_ORDER_SERVICE',
          ownershipVerified: false,
          orderId: 'other-user',
        },
      ],
    })

    expect(context.authoritativeOrders.map((item) => item.orderId)).toEqual(['owned'])
    expect(handoffOrderProducts(context.authoritativeOrders[0])).toBe('耳机')
  })

  it('rejects unknown schemas and bounds conversation rendering', () => {
    expect(normalizeHandoffContext({ schemaVersion: 'unknown' })).toBeNull()
    const context = normalizeHandoffContext({
      schemaVersion: 'aishop-support-handoff/v1',
      recentConversation: Array.from({ length: 8 }, (_, index) => ({
        role: 'USER',
        content: `${index}${'x'.repeat(220)}`,
      })),
    })
    expect(context.recentConversation).toHaveLength(6)
    expect(context.recentConversation.every((item) => item.content.length <= 200)).toBe(true)
  })

  it('formats model hints as explicitly separate data', () => {
    expect(formatHandoffHints({ orderId: 'hint-1' })).toContain('hint-1')
    expect(formatHandoffHints({})).toBe('无')
  })
})
