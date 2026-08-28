<template>
  <section class="analyst-page">
    <header class="page-heading">
      <h2>AI 经营分析</h2>
      <div class="heading-actions">
        <el-segmented v-model="mode" :options="modeOptions" />
        <el-tag v-if="result" :type="statusType(result.outcome || result.status)" effect="plain">
          {{ statusText(result.outcome || result.status) }}
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
      <el-button
        v-if="lastQuestion"
        type="success"
        plain
        :loading="exporting"
        :disabled="!canExportAnalyticsResult(analysisResult)"
        :title="canExportAnalyticsResult(analysisResult) ? '导出当前冻结结果' : '当前回答没有可导出的冻结结果'"
        @click="requestExport"
      >
        异步导出
      </el-button>
    </div>
    <div v-else class="inventory-toolbar">
      <el-tag type="info" effect="plain">固定 28 天 EWMA · 14 天复核周期</el-tag>
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
      v-else-if="snapshotExpired"
      title="冻结结果已过期，请重新发起分析后再翻页或导出。"
      type="warning"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="result?.completion === 'PARTIAL'"
      title="仅部分指标分支成功；以下内容不能计作完整可信回答。"
      type="warning"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="result?.warnings?.length"
      :title="warningText(result.warnings)"
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
            查看运行追踪
          </el-button>
        </div>
        <p>{{ failureText(result.status) }}</p>
        <small v-if="result.runId">运行编号 {{ result.runId }}</small>
      </section>
    </template>

    <template v-else-if="result && mode === 'inventory'">
      <div class="run-meta">
        <span>运行编号 {{ result.runId || '—' }}</span>
        <span>{{ result.lookbackDays || lookbackDays }} 天需求信号</span>
        <span>{{ result.latencyMs == null ? '—' : `${result.latencyMs} ms` }}</span>
        <el-tag v-for="view in result.lineage || []" :key="view" size="small" type="info">
          {{ view }}
        </el-tag>
        <el-button v-if="result.runId" link type="primary" :icon="Connection" @click="openTrace">
          查看运行追踪
        </el-button>
      </div>
      <div class="metric-grid inventory-summary">
        <article class="metric-cell"><span>风险 SKU</span><strong>{{ result.summary?.riskSkuCount || 0 }}</strong></article>
        <article class="metric-cell"><span>紧急</span><strong>{{ result.summary?.criticalCount || 0 }}</strong></article>
        <article class="metric-cell"><span>高优先级</span><strong>{{ result.summary?.highCount || 0 }}</strong></article>
        <article class="metric-cell"><span>建议补货总量</span><strong>{{ result.summary?.suggestedReplenishQuantity || 0 }}</strong></article>
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
            <el-table-column label="在途" prop="inboundQuantity" width="90" />
            <el-table-column label="EWMA 日需求" width="120">
              <template #default="{ row }">{{ formatValue(row.ewmaDailyDemand) }}</template>
            </el-table-column>
            <el-table-column label="覆盖天数" width="105">
              <template #default="{ row }">{{ formatValue(row.coverageDays) }}</template>
            </el-table-column>
            <el-table-column label="再订货点" width="105">
              <template #default="{ row }">{{ formatValue(row.reorderPoint) }}</template>
            </el-table-column>
            <el-table-column label="MOQ" prop="minOrderQuantity" width="80" />
            <el-table-column label="建议补货" prop="suggestedReplenishQuantity" width="105" />
            <el-table-column label="置信度" width="95">
              <template #default="{ row }">{{ formatPercent(row.confidence) }}</template>
            </el-table-column>
            <el-table-column label="建议人工动作" prop="suggestedAction" min-width="300" />
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
      <section v-if="result.outcome === 'CLARIFY' || result.status === 'NEEDS_CLARIFICATION'" class="clarification-band">
        <div>
          <span>需要确认口径</span>
          <strong>{{ result.clarificationQuestion }}</strong>
        </div>
        <div class="clarification-actions">
          <el-button
            v-for="option in result.clarificationOptions || []"
            :key="option.choiceId"
            size="small"
            :loading="clarifying"
            @click="clarify(option.choiceId)"
          >
            {{ option.label }}
          </el-button>
        </div>
      </section>

      <section
        v-else-if="result.outcome === 'ABSTAIN' || result.outcome === 'DENY'"
        class="decision-band"
        :class="{ denied: result.outcome === 'DENY' }"
      >
        <div class="answer-heading">
          <span>{{ result.outcome === 'DENY' ? '请求已拒绝' : '当前范围无法回答' }}</span>
          <el-tag :type="result.outcome === 'DENY' ? 'danger' : 'warning'" effect="plain">
            {{ result.reasonCode || result.status }}
          </el-tag>
        </div>
        <p>{{ result.answer }}</p>
        <small>{{ result.runId || result.requestId || '—' }}</small>
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
          <span v-if="result.catalogVersion">Catalog {{ result.catalogVersion }}</span>
          <span v-if="result.dataAsOf">dataAsOf {{ result.dataAsOf }}</span>
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
                :label="columnLabel(column)"
                min-width="150"
                show-overflow-tooltip
              >
                <template #default="{ row }">{{ formatCell(row[column], column) }}</template>
              </el-table-column>
            </el-table>
          </div>
          <el-button
            v-if="result.nextCursor"
            type="primary"
            plain
            :loading="loadingMore"
            @click="loadMore"
          >
            加载下一页
          </el-button>
        </section>

        <el-alert
          v-if="result.causalCaution"
          class="causal-caution"
          :title="result.causalCaution"
          type="warning"
          show-icon
          :closable="false"
        />

        <section v-if="result.diagnosisTree?.length" class="data-section diagnosis-section">
          <header>
            <h3>指标树与诊断分支</h3>
            <span class="section-note">各分支相互独立执行，单支失败不会覆盖已完成结果</span>
          </header>
          <el-table :data="result.diagnosisTree" stripe>
            <el-table-column label="分支" prop="branchId" width="150" />
            <el-table-column label="验证目标" prop="purpose" min-width="240" show-overflow-tooltip />
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <el-tag :type="row.status === 'SUCCEEDED' ? 'success' : row.status === 'EMPTY_RESULT' ? 'warning' : 'danger'">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="数据血缘" min-width="260">
              <template #default="{ row }">{{ (row.lineage || []).join('、') || '—' }}</template>
            </el-table-column>
          </el-table>
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
                <dd>
                  {{ item.definition }}
                  <small v-if="item.semanticView" class="definition-source">
                    {{ item.semanticView }} / {{ item.branchId }}
                  </small>
                </dd>
              </template>
            </dl>
            <details v-if="result.explain?.length">
              <summary>数据库执行计划（EXPLAIN）</summary>
              <pre>{{ pretty(result.explain) }}</pre>
            </details>
            <div v-if="result.queries?.length" class="branch-queries">
              <h4>各分支 SQL 与执行计划</h4>
              <el-collapse>
                <el-collapse-item
                  v-for="query in result.queries"
                  :key="query.branchId"
                  :name="query.branchId"
                  :title="`${query.branchId} · ${query.purpose || '指标分支'} · ${statusText(query.status)}`"
                >
                  <div class="query-source">血缘：{{ (query.lineage || []).join('、') || '—' }}</div>
                  <pre v-if="query.sql">{{ query.sql }}</pre>
                  <details v-if="query.explain?.length">
                    <summary>EXPLAIN 摘要</summary>
                    <pre>{{ pretty(query.explain) }}</pre>
                  </details>
                </el-collapse-item>
              </el-collapse>
            </div>
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
import { reasonLabel } from '@/utils/agentDisplay.js'
import {
  canExportAnalyticsResult,
  chartNumericRows,
  formatAnalyticsCell,
  isAnalyticsNumeric,
  isFailedAnalyticsResult,
  isSnapshotExpiredCode,
  mergeFrozenPage,
} from '@/utils/dataAnalyst.js'

const { proxy } = getCurrentInstance()
const mode = ref('analysis')
const modeOptions = [
  { label: '经营问答', value: 'analysis' },
  { label: '库存建议', value: 'inventory' },
]
const question = ref('')
const lastQuestion = ref('')
const lookbackDays = ref(28)
const loading = ref(false)
const loadingMore = ref(false)
const clarifying = ref(false)
const exporting = ref(false)
const exportJob = ref(null)
const analysisResult = ref(null)
const inventoryResult = ref(null)
const result = computed(() => mode.value === 'analysis' ? analysisResult.value : inventoryResult.value)
const errorMessage = ref('')
const snapshotExpired = ref(false)
const chartRef = ref(null)
let chart

const columnLabels = {
  stat_date: '日期', product_id: '商品 ID', product_name: '商品名称',
  paid_order_count: '已支付订单数', paid_amount: '支付金额',
  completed_refund_count: '已完成退款数', completed_refund_amount: '退款金额',
  net_paid_amount: '净支付金额', sold_quantity: '销售件数',
  order_item_amount: '订单商品金额', refunded_quantity: '退款件数',
  sku_id: 'SKU ID', stock: '当前库存', risk_status: '库存风险',
  run_count: '运行次数', success_count: '成功次数', failure_count: '失败次数',
  handoff_count: '转人工次数', avg_latency_ms: '平均耗时（毫秒）',
  input_tokens: '输入 Token', output_tokens: '输出 Token', cost_cny: '成本（元）',
  retrieval_mode: '检索模式', impression_count: '曝光数', click_count: '点击数',
  add_to_cart_count: '加购数', payment_count: '支付数', click_through_rate: '点击率',
  cart_rate: '加购率', payment_rate: '支付率', refund_count: '退款数',
  return_count: '退货数', negative_review_count: '低分评价数',
  support_contact_count: '售后联系数', repeat_purchase_count: '复购数',
  quote_count: '报价快照数', coupon_available_count: '可用优惠数',
  avg_base_price: '平均基础价', avg_estimated_payable: '平均估算到手价',
  in_stock_quote_count: '可购买报价数', paid_order_count: '已支付订单数',
  shipped_order_count: '已发货订单数', completed_order_count: '已完成订单数',
  cancelled_order_count: '已取消订单数', refund_request_count: '退款申请数',
  refund_completed_count: '退款完成数', refund_completed_amount: '退款完成金额',
  sku_key: 'SKU', current_stock: '当前库存', inbound_quantity: '在途量',
  ewma_daily_demand: 'EWMA 日需求', lead_time_days: '交期（天）', safety_stock: '安全库存',
  review_period_days: '复核周期（天）', min_order_quantity: 'MOQ', reorder_point: '再订货点',
  suggested_replenish_quantity: '建议补货量', coverage_days: '库存覆盖天数', confidence: '预测置信度',
}
const columnLabel = (value) => columnLabels[value] || value
const warningText = (warnings) => (warnings || []).map(reasonLabel).join('；')
const metricCards = computed(() => {
  const rows = result.value?.rows || []
  const row = rows[rows.length - 1] || {}
  const x = result.value?.chart?.x
  return (result.value?.columns || [])
    .filter((name) => name !== x && isAnalyticsNumeric(result.value?.columnTypes, name, row[name]))
    .slice(0, 4)
    .map((name) => ({ name, value: row[name] }))
})

const captureAnalysisError = (payload) => {
  if (!payload?.data) return false
  analysisResult.value = payload.data
  snapshotExpired.value = isSnapshotExpiredCode(payload.data.reasonCode || payload.data.status)
  errorMessage.value = ''
  return true
}

const captureSnapshotError = (payload) => {
  const code = payload?.data?.reasonCode || payload?.data?.status
  if (isSnapshotExpiredCode(code)) {
    snapshotExpired.value = true
    errorMessage.value = ''
    return true
  }
  if (payload?.info) errorMessage.value = payload.info
  return Boolean(payload?.data)
}

const ask = async () => {
  const normalized = question.value.trim()
  if (!normalized) {
    ElMessage.warning('请输入经营分析问题')
    return
  }
  loading.value = true
  errorMessage.value = ''
  snapshotExpired.value = false
  chart?.dispose()
  chart = null
  let shouldRender = false
  let structuredError = false
  try {
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystAsk,
      params: { question: normalized },
      showLoading: false,
      showError: false,
      errorCallback: (payload) => {
        structuredError = captureAnalysisError(payload)
        shouldRender = structuredError
      },
      timeout: 55000,
    })
    if (!response?.data) {
      if (!structuredError) {
        analysisResult.value = null
        errorMessage.value = '分析服务暂时不可用，请稍后重试。'
      }
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

const requestExport = async () => {
  if (!canExportAnalyticsResult(analysisResult.value) || exporting.value) return
  exporting.value = true
  try {
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystExport,
      params: { resultSetId: analysisResult.value.resultSetId },
      showLoading: false,
      showError: false,
      errorCallback: captureSnapshotError,
      timeout: 15000,
    })
    exportJob.value = response?.data || null
    if (exportJob.value?.jobId) void pollExport(exportJob.value.jobId)
  } finally {
    exporting.value = false
  }
}

const pollExport = async (jobId) => {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystExportStatus,
      params: { jobId },
      showLoading: false,
      showError: false,
      errorCallback: captureSnapshotError,
    })
    exportJob.value = response?.data || exportJob.value
    if (exportJob.value?.status === 'COMPLETED') {
      const blob = await proxy.Request({
        url: proxy.Api.dataAnalystExportDownload,
        params: { jobId },
        responseType: 'blob',
        showLoading: false,
        showError: false,
        errorCallback: captureSnapshotError,
      })
      if (blob) {
        const link = document.createElement('a')
        link.href = URL.createObjectURL(blob)
        link.download = `${jobId}.json`
        link.click()
        URL.revokeObjectURL(link.href)
      }
      return
    }
    if (exportJob.value?.status === 'FAILED') return
  }
}

const loadMore = async () => {
  const current = analysisResult.value
  if (!current?.nextCursor || loadingMore.value) return
  loadingMore.value = true
  errorMessage.value = ''
  try {
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystPage,
      params: { cursor: current.nextCursor, pageSize: 50 },
      showLoading: false,
      showError: false,
      errorCallback: captureSnapshotError,
    })
    if (!response?.data) return
    try {
      analysisResult.value = mergeFrozenPage(current, response.data)
    } catch {
      errorMessage.value = '分页结果完整性校验失败，请重新发起分析。'
      return
    }
    await nextTick()
    renderChart()
  } finally {
    loadingMore.value = false
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

const clarify = async (choiceId) => {
  const token = analysisResult.value?.clarificationToken
  if (!token || !choiceId || clarifying.value) return
  clarifying.value = true
  loading.value = true
  errorMessage.value = ''
  let structuredError = false
  try {
    const response = await proxy.Request({
      url: proxy.Api.dataAnalystClarify,
      params: { clarificationToken: token, choiceId },
      showLoading: false,
      showError: false,
      errorCallback: (payload) => {
        structuredError = captureAnalysisError(payload)
      },
      timeout: 55000,
    })
    if (response?.data) {
      analysisResult.value = response.data
    } else if (!structuredError) {
      errorMessage.value = '澄清状态不可用，请重新发起分析。'
      return
    }
  } finally {
    clarifying.value = false
    loading.value = false
  }
  await nextTick()
  renderChart()
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
  const numericRows = chartNumericRows(rows, spec, result.value?.columnTypes)
  chart?.dispose()
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
      data: numericRows.map((row) => row[name]),
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
const formatCell = (value, column) => formatAnalyticsCell(
  value,
  result.value?.columnTypes?.[column],
)
const formatPercent = (value) => {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(Number(value) * 100)}%`
}
const pretty = (value) => JSON.stringify(value || [], null, 2)
const statusText = (status) => ({
  ANSWER: '已回答',
  CLARIFY: '待确认口径',
  ABSTAIN: '超出当前范围',
  DENY: '已拒绝',
  SUCCEEDED: '查询成功',
  EMPTY_RESULT: '暂无数据',
  PARTIAL_METRIC_TREE: '部分指标完成',
  NEEDS_CLARIFICATION: '待确认口径',
  DISABLED: '未启用',
  DATA_ANALYST_REQUEST_TIMEOUT: '超时',
  DATA_ANALYST_BRANCH_FAILED: '指标分支失败',
  DATA_ANALYST_BRANCH_ERROR: '指标分支异常',
  DATA_ANALYST_MODEL_UNAVAILABLE: '分析模型不可用',
  DATA_ANALYST_PLAN_TIMEOUT: '计划生成超时',
  DATA_ANALYST_PLAN_PARSE_FAILED: '计划解析失败',
  DATA_ANALYST_SQL_PARSE_FAILED: 'SQL 解析失败',
  DATA_ANALYST_SQL_TIMEOUT: 'SQL 生成超时',
  QUERY_TIMEOUT: '查询超时',
  DATABASE_UNAVAILABLE: '分析库不可用',
  ANALYTICS_POOL_UNAVAILABLE: '分析库未就绪',
  RESULT_SNAPSHOT_EXPIRED: '冻结结果已过期',
  RESULT_SNAPSHOT_UNAVAILABLE: '冻结结果不可用',
}[status] || status || '未知状态')
const isFailureResult = (value) => isFailedAnalyticsResult(value)
const failureText = (status) => {
  if (status === 'DISABLED') return 'AI 经营分析当前未启用。'
  if (status === 'DATA_ANALYST_REQUEST_TIMEOUT' || status === 'QUERY_TIMEOUT') return '本次分析超过执行预算，请缩小时间范围或问题范围后重试。'
  if (status === 'DATABASE_UNAVAILABLE' || status === 'ANALYTICS_POOL_UNAVAILABLE') return '只读分析数据库暂时不可用，请稍后重试。'
  if (String(status || '').startsWith('SQL_')) return '生成的查询未通过安全治理校验，本次未读取业务数据。'
  if (String(status || '').startsWith('DATA_ANALYST_')) return '分析模型或执行链路暂时不可用，本次未生成可信结论。'
  return '本次分析未生成可信结论，请稍后重试。'
}
const statusType = (status) => {
  if (status === 'ANSWER' || status === 'SUCCEEDED') return 'success'
  if (['CLARIFY', 'ABSTAIN', 'NEEDS_CLARIFICATION', 'EMPTY_RESULT', 'PARTIAL_METRIC_TREE'].includes(status)) return 'warning'
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
.data-section > header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.section-note, .query-source, .definition-source { color: var(--text3); font-size: 12px; }
.definition-source { display: block; margin-top: 3px; }
.causal-caution { margin-top: 2px; }
.branch-queries { display: grid; gap: 8px; margin-top: 16px; }
.branch-queries h4 { margin: 0; color: var(--text); font-size: 13px; }
.branch-queries :deep(.el-collapse-item__header) { font-size: 13px; }
.query-source { margin-bottom: 8px; }
.heading-actions { display: flex; align-items: center; gap: 10px; }
.query-bar {
  display: grid;
  grid-template-columns: minmax(260px, 760px) 96px 110px;
  gap: 10px;
  align-items: start;
}
.query-bar :deep(.el-button) { min-height: 32px; }
.inventory-toolbar { display: flex; align-items: center; gap: 10px; }
.clarification-band,
.answer-band,
.decision-band,
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
.decision-band { border-left-color: #d97706; }
.decision-band.denied { border-left-color: #c2415d; }
.failure-band { border-left-color: #c2415d; }
.answer-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.answer-band p,
.decision-band p,
.failure-band p { margin: 8px 0 0; color: var(--text); font-size: 15px; line-height: 1.7; }
.decision-band small { display: block; margin-top: 8px; color: var(--text3); font-size: 12px; }
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
