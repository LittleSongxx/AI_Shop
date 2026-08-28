const NUMERIC_TYPES = new Set(['DECIMAL', 'INTEGER', 'BIGINT', 'FLOAT', 'DOUBLE'])

export const analyticsColumnType = (columnTypes, column) => (
  String(columnTypes?.[column]?.type || '').toUpperCase()
)

export const isAnalyticsNumeric = (columnTypes, column, value) => (
  NUMERIC_TYPES.has(analyticsColumnType(columnTypes, column))
  || (typeof value === 'number' && Number.isFinite(value))
)

export const formatAnalyticsCell = (value, contract = {}) => {
  if (value == null || value === '') return '—'
  if (String(contract?.type || '').toUpperCase() === 'DECIMAL') {
    return String(value)
  }
  if (typeof value !== 'number') return value
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

export const toChartNumber = (value, contract = {}) => {
  const type = String(contract?.type || '').toUpperCase()
  if (!NUMERIC_TYPES.has(type) && typeof value !== 'number') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

export const chartNumericRows = (rows, spec, columnTypes) => (
  (rows || []).map((row) => {
    const clone = { ...row }
    for (const column of spec?.series || []) {
      clone[column] = toChartNumber(row?.[column], columnTypes?.[column])
    }
    return clone
  })
)

export const canExportAnalyticsResult = (result) => Boolean(
  result?.outcome === 'ANSWER'
  && result?.resultSetId
  && result?.resultHash
)

export const isFailedAnalyticsResult = (result) => (
  result?.completion === 'FAILED'
  || (result?.outcome == null
    && !['SUCCEEDED', 'EMPTY_RESULT', 'PARTIAL_METRIC_TREE'].includes(result?.status))
)

export const isSnapshotExpiredCode = (code) => [
  'RESULT_SNAPSHOT_EXPIRED',
  'EXPORT_NOT_FOUND',
  'EXPORT_ARTIFACT_EXPIRED',
].includes(String(code || ''))

export const mergeFrozenPage = (current, next) => {
  if (!current?.resultSetId || current.resultSetId !== next?.resultSetId) {
    throw new Error('RESULT_SET_MISMATCH')
  }
  if (!current?.resultHash || current.resultHash !== next?.resultHash) {
    throw new Error('RESULT_HASH_MISMATCH')
  }
  return {
    ...current,
    ...next,
    rows: [...(current.rows || []), ...(next.rows || [])],
    page: {
      ...(next.page || {}),
      offset: 0,
      size: (current.rows || []).length + (next.rows || []).length,
    },
  }
}
