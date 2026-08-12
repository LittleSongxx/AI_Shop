export function metricRatioText(metric) {
  const numerator = Number(metric?.numerator || 0)
  const denominator = Number(metric?.denominator || 0)
  return `${numerator} / ${denominator}`
}

export function metricRateText(metric) {
  if (metric?.rate == null) return '未采集'
  return `${(Number(metric.rate) * 100).toFixed(1)}%`
}

export function metricNumberText(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return '未采集'
  return Number(value).toFixed(digits)
}

export function evidenceSourceLabel(value) {
  return ({
    SYNTHETIC: '合成评测',
    LOCAL_PILOT: '本地试用',
    REAL_USER: '真实用户',
  })[value] || value || '全部来源'
}

export function realUserDisclosure(overview) {
  return overview?.realUserStatus === '已采集'
    ? '已采集'
    : 'REAL_USER 未采集，未使用合成或本地试用数据填充'
}
