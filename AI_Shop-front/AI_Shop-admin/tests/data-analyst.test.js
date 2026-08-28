import { describe, expect, it } from 'vitest'
import {
  canExportAnalyticsResult,
  chartNumericRows,
  formatAnalyticsCell,
  isFailedAnalyticsResult,
  isSnapshotExpiredCode,
  mergeFrozenPage,
} from '@/utils/dataAnalyst.js'

describe('Text2SQL foundation UI contract', () => {
  const columnTypes = {
    date: { type: 'DATE' },
    net_paid_amount: { type: 'DECIMAL', scale: 2, displayScale: 2, unit: 'CNY' },
    paid_order_count: { type: 'BIGINT' },
  }

  it('keeps DECIMAL strings intact for tables and makes chart-only numeric clones', () => {
    const rows = [{
      date: '2026-08-27',
      net_paid_amount: '12345678901234567890.20',
      paid_order_count: 7,
    }]
    const chartRows = chartNumericRows(
      rows,
      { x: 'date', series: ['net_paid_amount', 'paid_order_count'] },
      columnTypes,
    )

    expect(formatAnalyticsCell(rows[0].net_paid_amount, columnTypes.net_paid_amount))
      .toBe('12345678901234567890.20')
    expect(rows[0].net_paid_amount).toBe('12345678901234567890.20')
    expect(chartRows).not.toBe(rows)
    expect(chartRows[0].net_paid_amount).toBeTypeOf('number')
    expect(chartRows[0].paid_order_count).toBe(7)
  })

  it('appends only pages from the exact same frozen result and hash', () => {
    const current = {
      resultSetId: 'ars-1',
      resultHash: 'hash-1',
      rows: [{ id: 1 }],
      nextCursor: 'cursor-2',
    }
    const merged = mergeFrozenPage(current, {
      resultSetId: 'ars-1',
      resultHash: 'hash-1',
      rows: [{ id: 2 }],
      nextCursor: null,
      page: { offset: 1, size: 1, hasMore: false },
    })
    expect(merged.rows).toEqual([{ id: 1 }, { id: 2 }])
    expect(merged.page).toMatchObject({ offset: 0, size: 2, hasMore: false })
    expect(() => mergeFrozenPage(current, {
      resultSetId: 'ars-other', resultHash: 'hash-1', rows: [],
    })).toThrow('RESULT_SET_MISMATCH')
    expect(() => mergeFrozenPage(current, {
      resultSetId: 'ars-1', resultHash: 'tampered', rows: [],
    })).toThrow('RESULT_HASH_MISMATCH')
  })

  it('distinguishes partial answers, failures, and export eligibility', () => {
    expect(isFailedAnalyticsResult({ outcome: 'ANSWER', completion: 'PARTIAL' })).toBe(false)
    expect(isFailedAnalyticsResult({ outcome: null, completion: 'FAILED' })).toBe(true)
    expect(isFailedAnalyticsResult({ outcome: 'ABSTAIN', completion: 'NOT_APPLICABLE' })).toBe(false)
    expect(canExportAnalyticsResult({
      outcome: 'ANSWER', resultSetId: 'ars-1', resultHash: 'hash-1',
    })).toBe(true)
    expect(canExportAnalyticsResult({ outcome: 'ANSWER' })).toBe(false)
    expect(canExportAnalyticsResult({
      outcome: 'CLARIFY', resultSetId: 'ars-1', resultHash: 'hash-1',
    })).toBe(false)
  })

  it('recognizes snapshot and artifact expiry responses', () => {
    expect(isSnapshotExpiredCode('RESULT_SNAPSHOT_EXPIRED')).toBe(true)
    expect(isSnapshotExpiredCode('EXPORT_ARTIFACT_EXPIRED')).toBe(true)
    expect(isSnapshotExpiredCode('DATABASE_UNAVAILABLE')).toBe(false)
  })
})
