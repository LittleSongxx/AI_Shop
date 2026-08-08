<template>
  <section class="analyst-page">
    <header class="page-heading">
      <h2>AI 经营分析</h2>
      <div class="heading-actions">
        <el-segmented v-model="mode" :options="modeOptions" />
        <el-tag v-if="result" :type="statusType(result.status)" effect="plain">
          {{ statusText(result.status) }}
        </el-tag>
      </div>
    </header>

    <div v-if="mode === 'analysis'" class="query-bar">
      <el-input
        v-model="question"
        clearable
        maxlength="500"
        show-word-limit
        placeholder="例如：最近七天销售额和退款额趋势如何？"
        @keyup.enter="ask"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="ask">
        分析
      </el-button>
    </div>
    <div v-else class="inventory-toolbar">
      <el-select v-model="lookbackDays" class="lookback-select">
        <el-option label="近 14 天" :value="14" />
        <el-option label="近 30 天" :value="30" />
        <el-option label="近 60 天" :value="60" />
        <el-option label="近 90 天" :value="90" />
      </el-select>
      <el-button type="primary" :icon="Refresh" :loading="loading" @click="loadInventory">
        刷新建议
      </el-button>
    </div>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="result?.warnings?.length"
      :title="result.warnings.join('；')"
      type="warning"
      show-icon
      :closable="false"
    />

    <el-skeleton v-if="loading" :rows="7" animated />

    <template v-else-if="result && isFailureResult(result)">
      <section class="failure-band">
        <div class="answer-heading">
          <span>分析未完成</span>
          <el-button
            v-if="result.runId"
            link
            type="primary"
            :icon="Connection"
            @click="openTrace"
          >
            查看 Trace
          </el-button>
        </div>
        <p>{{ failureText(result.status) }}</p>
        <small v-if="result.runId">Run {{ result.runId }}</small>
      </section>
    </template>

    <template v-else-if="result && mode === 'inventory'">
      <div class="run-meta">
        <span>Run {{ result.runId || '—' }}</span>
        <span>{{ result.lookbackDays || lookbackDays }} 天需求信号</span>
        <span>{{ result.latencyMs == null ? '—' : `${result.latencyMs} ms` }}</span>
        <el-tag v-for="view in result.lineage || []" :key="view" size="small" type="info">
          {{ view }}
        </el-tag>
        <el-button v-if="result.runId" link type="primary" :icon="Connection" @click="openTrace">
          查看 Trace
        </el-button>
      </div>
      <div class="metric-grid inventory-summary">
        <article class="metric-cell"><span>风险 SKU</span><strong>{{ result.summary?.riskSkuCount || 0 }}</strong></article>
        <article class="metric-cell"><span>紧急</span><strong>{{ result.summary?.criticalCount || 0 }}</strong></article>
        <article class="metric-cell"><span>高优先级</span><strong>{{ result.summary?.highCount || 0 }}</strong></article>
        <article class="metric-cell"><span>执行方式</span><strong class="manual-label">人工待办</strong></article>
      </div>
      <section class="data-section">
        <header><h3>补货优先级</h3></header>
        <div class="table-wrap">
          <el-table :data="result.suggestions || []" stripe max-height="520">
            <el-table-column label="优先级" width="105">
              <template #default="{ row }"><el-tag :type="priorityType(row.priority)">{{ row.priority }}</el-tag></template>
            </el-table-column>
            <el-table-column label="商品 / SKU" min-width="230">
              <template #default="{ row }"><b>{{ row.productName || row.productId }}</b><small class="sku-line">{{ row.skuHash }}</small></template>
            </el-table-column>
            <el-table-column label="库存" prop="stock" width="90" />
            <el-table-column label="风险" prop="riskLevel" width="125" />
            <el-table-column label="商品净销量" prop="productNetUnits" width="120" />
            <el-table-column label="建议人工动作" prop="suggestedAction" min-width="280" />
          </el-table>
        </div>
      </section>
      <el-collapse v-if="result.metricDefinitions?.length" class="technical-details">
        <el-collapse-item title="优先级口径" name="inventory-metrics">
          <dl class="definitions">
            <template v-for="item in result.metricDefinitions" :key="item.name">
              <dt>{{ item.name }}</dt><dd>{{ item.definition }}</dd>
            </template>
          </dl>
        </el-collapse-item>
      </el-collapse>
    </template>

    <template v-else-if="result">
      <section v-if="result.status === 'NEEDS_CLARIFICATION'" class="clarification-band">
        <div>
          <span>需要确认口径</span>
          <strong>{{ result.clarificationQuestion }}</strong>
        </div>
        <div class="clarification-actions">
          <el-button
            v-for="metric in clarificationMetrics"
            :key="metric"
            size="small"
            @click="clarify(metric)"
          >
            按{{ metric }}
          </el-button>
        </div>
      </section>

      <template v-else>
        <section class="answer-band">
          <div class="answer-heading">
            <span>分析结论</span>
            <el-button
              v-if="result.runId"
              link
              type="primary"
              :icon="Connection"
              @click="openTrace"
            >
              查看 Trace
            </el-button>
          </div>
          <p>{{ result.answer || '当前查询没有生成可用结论。' }}</p>
          <ul v-if="result.highlights?.length">
            <li v-for="item in result.highlights" :key="item">{{ item }}</li>
          </ul>
        </section>

        <div class="run-meta">
          <span>Run {{ result.runId || '—' }}</span>
          <span>{{ result.latencyMs == null ? '—' : `${result.latencyMs} ms` }}</span>
          <span>{{ result.rows?.length || 0 }} 行</span>
          <el-tag v-for="view in result.lineage || []" :key="view" size="small" type="info">
            {{ view }}
          </el-tag>
        </div>

        <div v-if="metricCards.length" class="metric-grid">
          <article v-for="metric in metricCards" :key="metric.name" class="metric-cell">
            <span>{{ metric.name }}</span>
            <strong>{{ formatValue(metric.value) }}</strong>
          </article>
        </div>

        <section v-if="result.chart && result.rows?.length" class="data-section">
          <header><h3>趋势与分布</h3></header>
          <div ref="chartRef" class="chart" />
        </section>

        <section v-if="result.rows?.length" class="data-section">
          <header><h3>聚合数据</h3></header>
          <div class="table-wrap">
            <el-table :data="result.rows" stripe max-height="430">
              <el-table-column
                v-for="column in result.columns"
                :key="column"
                :prop="column"
                :label="column"
                min-width="150"
                show-overflow-tooltip
              >
                <template #default="{ row }">{{ formatValue(row[column]) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </section>

        <el-collapse class="technical-details">
          <el-collapse-item title="SQL、指标口径与执行计划" name="sql">
            <div v-if="result.interpretation" class="interpretation">
              {{ result.interpretation }}
            </div>
            <pre v-if="result.sql">{{ result.sql }}</pre>
            <dl v-if="result.metricDefinitions?.length" class="definitions">
              <template v-for="item in result.metricDefinitions" :key="item.name">
                <dt>{{ item.name }}</dt>
                <dd>{{ item.definition }}</dd>
              </template>
            </dl>
            <details v-if="result.explain?.length">
              <summary>EXPLAIN</summary>
              <pre>{{ pretty(result.explain) }}</pre>
            </details>
          </el-collapse-item>
        </el-collapse>
      </template>
    </template>

    <el-empty v-else :description="mode === 'inventory' ? '暂无补货建议' : '暂无分析结果'" :image-size="88" />
  </section>
</template>

<script setup>
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Connection, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'

const { proxy } = getCurrentInstance()
const mode = ref('analysis')
const modeOptions = [
  { label: '经营问答', value: 'analysis' },
  { label: '库存建议', value: 'inventory' },
]
const question = ref('')
const lastQuestion = ref('')
const lookbackDays = ref(30)
const loading = ref(false)
const analysisResult = ref(null)
const inventoryResult = ref(null)
const result = computed(() => mode.value === 'analysis' ? analysisResult.value : inventoryResult.value)
const errorMessage = ref('')
const chartRef = ref(null)
let chart

const clarificationMetrics = ['销售金额', '销售件数', '订单数']
const metricCards = computed(() => {
  const rows = result.value?.rows || []
  const row = rows[rows.length - 1] || {}
  const x = result.value?.chart?.x
  return (result.value?.columns || [])
    .filter((name) => name !== x && typeof row[name] === 'number')
    .slice(0, 4)
    .map((name) => ({ name, value: row[name] }))
})

const ask = async () => {
  const normalized = question.value.trim()
  if (!normalized) {
    ElMessage.warning('请输入经营分析问题')
    return
  }
  loading.value = true
  errorMessage.value = ''
  chart?.dispose()
  chart = null
  let shouldRender = false
  try {
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystAsk,
      params: { question: normalized },
      showLoading: false,
      timeout: 55000,
    })
    if (!response?.data) {
      analysisResult.value = null
      errorMessage.value = '分析服务暂时不可用，请稍后重试。'
      return
    }
    lastQuestion.value = normalized
    analysisResult.value = response.data
    shouldRender = true
  } finally {
    loading.value = false
  }
  if (shouldRender) {
    await nextTick()
    renderChart()
  }
}

const loadInventory = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await proxy.Request({
      url: proxy.Api.inventoryOpsSuggestions,
      params: { lookbackDays: lookbackDays.value, limit: 50 },
      showLoading: false,
      timeout: 15000,
    })
    if (!response?.data) {
      inventoryResult.value = null
      errorMessage.value = '库存建议服务暂时不可用，请稍后重试。'
      return
    }
    inventoryResult.value = response.data
  } finally {
    loading.value = false
  }
}

const clarify = (metric) => {
  question.value = `${lastQuestion.value || question.value}，按${metric}统计`
  ask()
}

const openTrace = () => {
  proxy.$router.push({
    path: '/setting/agentQuality',
    query: { runId: result.value.runId },
  })
}

const renderChart = () => {
  const spec = result.value?.chart
  const rows = result.value?.rows || []
  if (!chartRef.value || !spec?.x || !spec.series?.length || !rows.length) return
  chart = echarts.init(chartRef.value)
  chart.setOption({
    animationDuration: 260,
    color: ['#2563eb', '#0f9d73', '#d97706', '#c2415d'],
    tooltip: { trigger: 'axis' },
    legend: { top: 0, data: spec.series },
    grid: { left: 16, right: 18, top: 48, bottom: 12, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row[spec.x]),
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e5e7eb' } } },
    series: spec.series.map((name) => ({
      name,
      type: spec.type === 'bar' ? 'bar' : 'line',
      data: rows.map((row) => row[name]),
      smooth: false,
      symbolSize: 6,
      barMaxWidth: 44,
    })),
  })
}

const formatValue = (value) => {
  if (value == null || value === '') return '—'
  if (typeof value !== 'number') return value
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}
const pretty = (value) => JSON.stringify(value || [], null, 2)
const statusText = (status) => ({
  SUCCEEDED: '查询成功',
  EMPTY_RESULT: '暂无数据',
  NEEDS_CLARIFICATION: '待确认口径',
  DISABLED: '未启用',
  DATA_ANALYST_REQUEST_TIMEOUT: '超时',
  QUERY_TIMEOUT: '查询超时',
  DATABASE_UNAVAILABLE: '分析库不可用',
  ANALYTICS_POOL_UNAVAILABLE: '分析库未就绪',
}[status] || status || '未知状态')
const isFailureResult = (value) => !['SUCCEEDED', 'EMPTY_RESULT', 'NEEDS_CLARIFICATION'].includes(value?.status)
const failureText = (status) => {
  if (status === 'DISABLED') return 'AI 经营分析当前未启用。'
  if (status === 'DATA_ANALYST_REQUEST_TIMEOUT' || status === 'QUERY_TIMEOUT') return '本次分析超过执行预算，请缩小时间范围或问题范围后重试。'
  if (status === 'DATABASE_UNAVAILABLE' || status === 'ANALYTICS_POOL_UNAVAILABLE') return '只读分析数据库暂时不可用，请稍后重试。'
  if (String(status || '').startsWith('SQL_')) return '生成的查询未通过安全治理校验，本次未读取业务数据。'
  if (String(status || '').startsWith('DATA_ANALYST_')) return '分析模型或执行链路暂时不可用，本次未生成可信结论。'
  return '本次分析未生成可信结论，请稍后重试。'
}
const statusType = (status) => {
  if (status === 'SUCCEEDED') return 'success'
  if (status === 'NEEDS_CLARIFICATION' || status === 'EMPTY_RESULT') return 'warning'
  return 'danger'
}
const priorityType = (priority) => ({
  CRITICAL: 'danger',
  HIGH: 'warning',
  MEDIUM: 'primary',
  LOW: 'info',
}[priority] || 'info')
const resizeChart = () => chart?.resize()

onMounted(() => window.addEventListener('resize', resizeChart))
watch(mode, async (value) => {
  errorMessage.value = ''
  chart?.dispose()
  chart = null
  if (value === 'inventory' && !inventoryResult.value) {
    await loadInventory()
  } else if (value === 'analysis' && analysisResult.value) {
    await nextTick()
    renderChart()
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})
</script>

<style scoped>
.analyst-page {
  display: grid;
  gap: 16px;
  min-width: 0;
  padding: 20px 24px 28px;
}
.page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.page-heading h2,
.data-section h3 {
  margin: 0;
  color: var(--text);
  letter-spacing: 0;
}
.page-heading h2 { font-size: 20px; }
.data-section h3 { font-size: 14px; }
.heading-actions { display: flex; align-items: center; gap: 10px; }
.query-bar {
  display: grid;
  grid-template-columns: minmax(260px, 760px) 96px;
  gap: 10px;
  align-items: start;
}
.query-bar :deep(.el-button) { min-height: 32px; }
.inventory-toolbar { display: flex; align-items: center; gap: 10px; }
.lookback-select { width: 140px; }
.clarification-band,
.answer-band,
.failure-band {
  border-left: 3px solid #d97706;
  background: var(--surface2);
  padding: 14px 16px;
}
.clarification-band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.clarification-band div:first-child { display: grid; gap: 5px; }
.clarification-band span,
.answer-heading span { color: var(--text3); font-size: 12px; }
.clarification-band strong { color: var(--text); font-size: 14px; }
.clarification-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.answer-band { border-left-color: #0f9d73; }
.failure-band { border-left-color: #c2415d; }
.answer-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.answer-band p,
.failure-band p { margin: 8px 0 0; color: var(--text); font-size: 15px; line-height: 1.7; }
.failure-band small { display: block; margin-top: 8px; color: var(--text3); font-size: 12px; overflow-wrap: anywhere; }
.answer-band ul { margin: 10px 0 0; padding-left: 18px; color: var(--text2); }
.answer-band li { margin: 4px 0; line-height: 1.55; }
.run-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; color: var(--text3); font-size: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.metric-cell { display: grid; gap: 8px; min-width: 0; padding: 13px 15px; border-right: 1px solid var(--border); }
.metric-cell:last-child { border-right: 0; }
.metric-cell span { overflow-wrap: anywhere; color: var(--text3); font-size: 12px; }
.metric-cell strong { overflow-wrap: anywhere; color: var(--text); font-size: 20px; font-variant-numeric: tabular-nums; }
.metric-cell .manual-label { font-size: 15px; }
.sku-line { display: block; margin-top: 4px; color: var(--text3); font-size: 11px; overflow-wrap: anywhere; }
.data-section { display: grid; gap: 10px; min-width: 0; padding-top: 4px; }
.chart { width: 100%; height: 320px; min-height: 260px; border-top: 1px solid var(--border); }
.table-wrap { width: 100%; min-width: 0; overflow-x: auto; border-top: 1px solid var(--border); }
.technical-details { border-top: 1px solid var(--border); }
.interpretation { margin-bottom: 10px; color: var(--text2); font-size: 13px; }
pre { max-width: 100%; margin: 8px 0; padding: 12px; overflow: auto; border-radius: 6px; background: #f5f7f8; color: #263238; font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.definitions { display: grid; grid-template-columns: minmax(130px, 210px) minmax(0, 1fr); margin: 14px 0; font-size: 13px; }
.definitions dt,
.definitions dd { margin: 0; padding: 8px 10px; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
.definitions dt { color: var(--text); font-weight: 600; }
.definitions dd { color: var(--text2); }
details summary { color: var(--primary); font-size: 13px; cursor: pointer; }
@media (max-width: 820px) {
  .analyst-page { padding: 14px 12px 22px; }
  .page-heading { align-items: flex-start; flex-direction: column; }
  .heading-actions { width: 100%; justify-content: space-between; }
  .query-bar { grid-template-columns: minmax(0, 1fr) 88px; }
  .clarification-band { align-items: flex-start; flex-direction: column; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-cell:nth-child(2) { border-right: 0; }
  .metric-cell:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
  .definitions { grid-template-columns: 1fr; }
  .definitions dt { border-bottom: 0; padding-bottom: 2px; }
  .definitions dd { padding-top: 2px; }
}
@media (max-width: 520px) {
  .query-bar { grid-template-columns: 1fr; }
  .inventory-toolbar { align-items: stretch; flex-direction: column; }
  .lookback-select { width: 100%; }
  .query-bar :deep(.el-button) { width: 100%; }
  .metric-grid { grid-template-columns: 1fr; }
  .metric-cell { border-right: 0; border-bottom: 1px solid var(--border); }
  .metric-cell:last-child { border-bottom: 0; }
  .chart { height: 280px; }
}
</style>
