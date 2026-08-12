<template>
  <div class="evidence-center">
    <section class="toolbar-band">
      <div class="filter-row">
        <el-select v-model="filters.batchId" clearable filterable placeholder="全部批次" class="filter-control">
          <el-option
            v-for="batch in batches"
            :key="batch.batchId"
            :label="`${batch.name} · ${sourceLabel(batch.evidenceSource)}`"
            :value="batch.batchId"
          />
        </el-select>
        <el-select v-model="filters.evidenceSource" clearable placeholder="全部样本来源" class="filter-control">
          <el-option label="合成评测" value="SYNTHETIC" />
          <el-option label="本地试用" value="LOCAL_PILOT" />
          <el-option label="真实用户" value="REAL_USER" />
        </el-select>
        <el-date-picker
          v-model="filters.timeRange"
          type="datetimerange"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          class="date-control"
        />
        <el-button type="primary" :loading="metricsLoading" :disabled="!canReadMetrics" @click="loadMetrics">
          刷新指标
        </el-button>
        <el-button v-if="canPilot" @click="openCreateBatch">新建试用批次</el-button>
      </div>
      <div class="identity-row">
        <span>{{ principal.displayName || principal.account || '管理员' }}</span>
        <el-tag v-for="role in principal.roles" :key="role" size="small" effect="plain">{{ role }}</el-tag>
      </div>
    </section>

    <el-alert
      v-if="principalLoaded && !canReadMetrics"
      title="当前角色没有聚合指标读取权限"
      description="AI_OPERATOR 或 DATA_ANALYST 可读取指标，AUDITOR 可查看批次与审计信息。"
      type="warning"
      :closable="false"
      show-icon
    />

    <template v-if="canReadMetrics">
      <section class="disclosure-band" v-loading="metricsLoading">
        <div>
          <b>证据口径</b>
          <span>{{ overview.schemaVersion || 'aishop-pilot-metrics/v1' }}</span>
          <span>已验证成功只计验证器通过、用户明确确认或人工解决</span>
        </div>
        <el-tag :type="overview.realUserStatus === '已采集' ? 'success' : 'info'" effect="plain">
          {{ realUserText }}
        </el-tag>
      </section>

      <section class="metric-section" v-loading="metricsLoading">
        <header class="section-head">
          <div><h2>任务与业务结果</h2><p>所有比率同时展示分子、分母和时间窗口。</p></div>
        </header>
        <div class="metric-grid">
          <article class="metric-card">
            <span>已执行任务</span>
            <strong>{{ overview.tasks?.executed ?? 0 }}</strong>
            <small>终态 {{ overview.tasks?.terminal ?? 0 }}</small>
          </article>
          <article class="metric-card">
            <span>Verified Success</span>
            <strong>{{ rateText(overview.tasks?.verifiedSuccess) }}</strong>
            <small>{{ ratioText(overview.tasks?.verifiedSuccess) }} · 任务级</small>
          </article>
          <article class="metric-card">
            <span>FCR</span>
            <strong>{{ rateText(overview.tasks?.fcr24h) }}</strong>
            <small>{{ ratioText(overview.tasks?.fcr24h) }} · 24 小时</small>
          </article>
          <article class="metric-card">
            <span>推荐点击</span>
            <strong>{{ rateText(overview.funnel?.clickWithin24h) }}</strong>
            <small>{{ ratioText(overview.funnel?.clickWithin24h) }} · 24 小时</small>
          </article>
          <article class="metric-card">
            <span>支付结果</span>
            <strong>{{ rateText(overview.funnel?.paymentWithin7d) }}</strong>
            <small>{{ ratioText(overview.funnel?.paymentWithin7d) }} · 7 天</small>
          </article>
          <article class="metric-card">
            <span>负向结果</span>
            <strong>{{ rateText(overview.funnel?.negativeOutcomeWithin7d) }}</strong>
            <small>{{ ratioText(overview.funnel?.negativeOutcomeWithin7d) }} · 7 天</small>
          </article>
        </div>
        <el-empty v-if="!overview.tasks?.executed" description="当前筛选条件下尚未采集证据运行" :image-size="72" />
      </section>

      <section class="metric-section" v-loading="metricsLoading">
        <header class="section-head">
          <div><h2>性能与成本</h2><p>P99 仅在样本数达到 100 时报告，避免小样本伪精确。</p></div>
        </header>
        <div class="performance-grid">
          <article class="performance-panel">
            <header><b>端到端延迟</b><span>n={{ performance.latencyMs?.sampleSize || 0 }}</span></header>
            <dl>
              <div><dt>P50</dt><dd>{{ durationText(performance.latencyMs?.p50) }}</dd></div>
              <div><dt>P95</dt><dd>{{ durationText(performance.latencyMs?.p95) }}</dd></div>
              <div><dt>P99</dt><dd>{{ durationText(performance.latencyMs?.p99) }}</dd></div>
            </dl>
            <small>{{ performance.latencyMs?.p99Status || '样本少于 100，未报告' }}</small>
          </article>
          <article class="performance-panel">
            <header><b>首 Token 延迟</b><span>n={{ performance.ttftMs?.sampleSize || 0 }}</span></header>
            <dl>
              <div><dt>P50</dt><dd>{{ durationText(performance.ttftMs?.p50) }}</dd></div>
              <div><dt>P95</dt><dd>{{ durationText(performance.ttftMs?.p95) }}</dd></div>
              <div><dt>P99</dt><dd>{{ durationText(performance.ttftMs?.p99) }}</dd></div>
            </dl>
            <small>{{ performance.ttftMs?.p99Status || '样本少于 100，未报告' }}</small>
          </article>
          <article class="performance-panel cost-panel">
            <header><b>调用与成本</b><span>{{ performance.runCount || 0 }} runs</span></header>
            <dl>
              <div><dt>模型调用 P50</dt><dd>{{ numberText(performance.modelCalls?.p50) }}</dd></div>
              <div><dt>工具调用 P50</dt><dd>{{ numberText(performance.toolCalls?.p50) }}</dd></div>
              <div><dt>Token</dt><dd>{{ tokenTotal }}</dd></div>
              <div><dt>总成本</dt><dd>¥{{ numberText(performance.costCny, 4) }}</dd></div>
              <div><dt>每成功任务</dt><dd>¥{{ numberText(performance.costPerVerifiedSuccessCny, 4) }}</dd></div>
            </dl>
          </article>
        </div>
      </section>
    </template>

    <section v-if="canReadBatches" class="batch-section">
      <header class="section-head">
        <div><h2>试用批次</h2><p>进入证据报告的运行必须归属明确批次与样本来源。</p></div>
        <el-button :loading="batchLoading" @click="loadBatches">刷新</el-button>
      </header>
      <div class="table-wrap">
        <el-table :data="batches" v-loading="batchLoading" stripe>
          <el-table-column label="批次" min-width="210">
            <template #default="{ row }"><b>{{ row.name }}</b><small class="block">{{ shortId(row.batchId) }}</small></template>
          </el-table-column>
          <el-table-column label="来源" width="110"><template #default="{ row }">{{ sourceLabel(row.evidenceSource) }}</template></el-table-column>
          <el-table-column label="状态" width="95"><template #default="{ row }"><el-tag :type="batchStatusType(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="参与者" width="115"><template #default="{ row }">{{ row.activeParticipantCount || 0 }} / {{ row.participantCount || 0 }}</template></el-table-column>
          <el-table-column label="创建时间" prop="createdAt" min-width="170" />
          <el-table-column label="操作" min-width="245" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openParticipants(row)">参与者</el-button>
              <el-button v-if="canPilot && row.status === 'DRAFT'" link type="primary" @click="changeBatch(row, 'start')">启动</el-button>
              <el-button v-if="canPilot && row.status !== 'CLOSED'" link type="warning" @click="changeBatch(row, 'close')">关闭</el-button>
              <el-dropdown v-if="canExport" @command="(format) => exportReport(row, format)">
                <el-button link type="primary">匿名导出</el-button>
                <template #dropdown><el-dropdown-menu><el-dropdown-item command="json">JSON</el-dropdown-item><el-dropdown-item command="csv">CSV</el-dropdown-item><el-dropdown-item command="markdown">Markdown</el-dropdown-item></el-dropdown-menu></template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <el-empty v-if="!batchLoading && !batches.length" description="尚未创建试用批次" :image-size="72" />
    </section>

    <el-dialog v-model="createDialog.show" title="新建试用批次" width="min(520px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="批次名称"><el-input v-model="createDialog.form.name" maxlength="120" /></el-form-item>
        <el-form-item label="样本来源">
          <el-segmented v-model="createDialog.form.evidenceSource" :options="sourceOptions" />
        </el-form-item>
        <el-form-item label="同意文本版本"><el-input v-model="createDialog.form.consentTextVersion" placeholder="例如 consent-v1" maxlength="64" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="createDialog.form.description" type="textarea" :rows="3" maxlength="1000" show-word-limit /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDialog.show = false">取消</el-button><el-button type="primary" :loading="createDialog.loading" @click="createBatch">创建</el-button></template>
    </el-dialog>

    <el-drawer v-model="participantDrawer.show" title="批次参与者" size="min(620px, 96vw)">
      <div v-if="canPilot && participantDrawer.batch?.status !== 'CLOSED'" class="participant-form">
        <el-input v-model="participantDrawer.userId" placeholder="用户 ID" />
        <el-input v-model="participantDrawer.pseudonym" placeholder="批次内伪名（可选）" />
        <el-button type="primary" :loading="participantDrawer.mutating" @click="registerParticipant">登记</el-button>
      </div>
      <el-table :data="participantDrawer.list" v-loading="participantDrawer.loading" stripe>
        <el-table-column label="伪名" prop="pseudonym" min-width="150" />
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'" size="small">{{ row.status === 'ACTIVE' ? '有效' : '已撤回' }}</el-tag></template></el-table-column>
        <el-table-column label="同意时间" prop="consentedAt" min-width="165" />
        <el-table-column v-if="canPilot" label="操作" width="90"><template #default="{ row }"><el-button v-if="row.status === 'ACTIVE'" link type="danger" @click="withdrawParticipant(row)">撤回</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!participantDrawer.loading && !participantDrawer.list.length" description="该批次尚无参与者" :image-size="72" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ADMIN_PERMISSION, hasAdminPermission, hasAnyAdminPermission, normalizeAdminPrincipal } from '@/utils/adminAccess.js'
import { evidenceSourceLabel, metricNumberText, metricRateText, metricRatioText, realUserDisclosure } from '@/utils/evidenceMetrics.js'

const { proxy } = getCurrentInstance()
const principal = ref(normalizeAdminPrincipal(null))
const principalLoaded = ref(false)
const batches = ref([])
const batchLoading = ref(false)
const metricsLoading = ref(false)
const overview = ref({})
const performance = ref({})
const filters = reactive({ batchId: '', evidenceSource: '', timeRange: [] })
const sourceOptions = [
  { label: '合成评测', value: 'SYNTHETIC' },
  { label: '本地试用', value: 'LOCAL_PILOT' },
  { label: '真实用户', value: 'REAL_USER' },
]
const createDialog = reactive({ show: false, loading: false, form: { name: '', description: '', evidenceSource: 'LOCAL_PILOT', consentTextVersion: 'consent-v1' } })
const participantDrawer = reactive({ show: false, loading: false, mutating: false, batch: null, list: [], userId: '', pseudonym: '' })

const canReadMetrics = computed(() => hasAnyAdminPermission(principal.value, [ADMIN_PERMISSION.ANALYTICS_READ, ADMIN_PERMISSION.AI_EVALUATE]))
const canReadBatches = computed(() => hasAnyAdminPermission(principal.value, [ADMIN_PERMISSION.AI_PILOT, ADMIN_PERMISSION.ANALYTICS_READ, ADMIN_PERMISSION.AUDIT_READ]))
const canPilot = computed(() => hasAdminPermission(principal.value, ADMIN_PERMISSION.AI_PILOT))
const canExport = computed(() => hasAdminPermission(principal.value, ADMIN_PERMISSION.ANALYTICS_EXPORT))
const tokenTotal = computed(() => Number(performance.value.tokens?.input || 0) + Number(performance.value.tokens?.output || 0))
const realUserText = computed(() => realUserDisclosure(overview.value))

const request = async (url, params = {}, options = {}) => {
  const result = await proxy.Request({ url, params, showLoading: false, ...options })
  return result?.data
}
const ratioText = metricRatioText
const rateText = metricRateText
const numberText = metricNumberText
const sourceLabel = evidenceSourceLabel
const durationText = (value) => value == null ? '未采集' : Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(2)}s` : `${Number(value).toFixed(0)}ms`
const shortId = (value) => String(value || '').length > 24 ? `${String(value).slice(0, 21)}...` : value
const batchStatusLabel = (value) => ({ DRAFT: '草稿', RUNNING: '进行中', CLOSED: '已关闭' })[value] || value
const batchStatusType = (value) => value === 'RUNNING' ? 'success' : value === 'CLOSED' ? 'info' : 'warning'
const metricParams = () => ({
  batchId: filters.batchId,
  evidenceSource: filters.evidenceSource,
  startAt: filters.timeRange?.[0] instanceof Date ? filters.timeRange[0].toISOString() : '',
  endAt: filters.timeRange?.[1] instanceof Date ? filters.timeRange[1].toISOString() : '',
})

const loadPrincipal = async () => {
  const data = await request(proxy.Api.adminMe, {}, { method: 'get' })
  principal.value = normalizeAdminPrincipal(data)
  principalLoaded.value = true
}
const loadBatches = async () => {
  if (!canReadBatches.value) return
  batchLoading.value = true
  try { batches.value = (await request(proxy.Api.agentPilotBatches, { limit: 100 })) || [] } finally { batchLoading.value = false }
}
const loadMetrics = async () => {
  if (!canReadMetrics.value) return
  metricsLoading.value = true
  try {
    const params = metricParams()
    const [overviewData, performanceData] = await Promise.all([
      request(proxy.Api.agentMetricsOverview, params),
      request(proxy.Api.agentMetricsPerformance, params),
    ])
    overview.value = overviewData || {}
    performance.value = performanceData || {}
  } finally { metricsLoading.value = false }
}
const openCreateBatch = () => { Object.assign(createDialog.form, { name: '', description: '', evidenceSource: 'LOCAL_PILOT', consentTextVersion: 'consent-v1' }); createDialog.show = true }
const createBatch = async () => {
  if (!createDialog.form.name.trim() || !createDialog.form.consentTextVersion.trim()) return ElMessage.warning('请填写批次名称和同意文本版本')
  createDialog.loading = true
  try {
    const created = await request(proxy.Api.agentPilotBatchCreate, createDialog.form)
    if (!created) return
    createDialog.show = false
    filters.batchId = created.batchId
    ElMessage.success('试用批次已创建')
    await loadBatches(); await loadMetrics()
  } finally { createDialog.loading = false }
}
const changeBatch = async (row, action) => {
  const verb = action === 'start' ? '启动' : '关闭'
  try { await ElMessageBox.confirm(`确定${verb}批次“${row.name}”吗？`, `${verb}批次`, { type: 'warning' }) } catch { return }
  const url = action === 'start' ? proxy.Api.agentPilotBatchStart : proxy.Api.agentPilotBatchClose
  if (await request(url, { batchId: row.batchId })) { ElMessage.success(`批次已${verb}`); await loadBatches() }
}
const openParticipants = async (row) => { participantDrawer.batch = row; participantDrawer.show = true; participantDrawer.userId = ''; participantDrawer.pseudonym = ''; await loadParticipants() }
const loadParticipants = async () => {
  if (!participantDrawer.batch) return
  participantDrawer.loading = true
  try { participantDrawer.list = (await request(proxy.Api.agentPilotParticipants, { batchId: participantDrawer.batch.batchId })) || [] } finally { participantDrawer.loading = false }
}
const registerParticipant = async () => {
  if (!participantDrawer.userId.trim()) return ElMessage.warning('请填写用户 ID')
  participantDrawer.mutating = true
  try {
    const data = await request(proxy.Api.agentPilotParticipantRegister, { batchId: participantDrawer.batch.batchId, userId: participantDrawer.userId, pseudonym: participantDrawer.pseudonym })
    if (!data) return
    participantDrawer.userId = ''; participantDrawer.pseudonym = ''; ElMessage.success('参与者已登记')
    await loadParticipants(); await loadBatches()
  } finally { participantDrawer.mutating = false }
}
const withdrawParticipant = async (row) => {
  try { await ElMessageBox.confirm(`确定撤回参与者 ${row.pseudonym} 吗？`, '撤回参与者', { type: 'warning' }) } catch { return }
  if (await request(proxy.Api.agentPilotParticipantWithdraw, { batchId: participantDrawer.batch.batchId, participantId: row.participantId })) { ElMessage.success('参与者已撤回'); await loadParticipants(); await loadBatches(); await loadMetrics() }
}
const exportReport = async (row, format) => {
  const blob = await proxy.Request({ url: proxy.Api.agentPilotReport, params: { batchId: row.batchId, format }, responseType: 'blob', showLoading: false })
  if (!(blob instanceof Blob)) return
  const suffix = format === 'markdown' ? 'md' : format
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href; link.download = `${row.batchId}.${suffix}`; link.click()
  window.setTimeout(() => URL.revokeObjectURL(href), 1000)
}

onMounted(async () => {
  try { await loadPrincipal(); await Promise.all([loadBatches(), loadMetrics()]) } finally { principalLoaded.value = true }
})
</script>

<style scoped lang="scss">
.evidence-center { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.toolbar-band, .metric-section, .batch-section { padding: 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.filter-control { width: 205px; }
.date-control { width: 360px; max-width: 100%; }
.identity-row { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-top: 12px; color: var(--text2); font-size: 12px; }
.disclosure-band { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 16px; border-left: 3px solid #0f766e; background: #f0fdfa; }
.disclosure-band > div { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
.disclosure-band span { color: #52636b; font-size: 12px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.section-head h2 { margin: 0; color: var(--text); font-size: 16px; }
.section-head p { margin: 5px 0 0; color: var(--text3); font-size: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(6, minmax(125px, 1fr)); gap: 10px; }
.metric-card { display: flex; flex-direction: column; min-width: 0; min-height: 104px; padding: 13px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface2); }
.metric-card span { color: var(--text3); font-size: 12px; }
.metric-card strong { margin: 12px 0 8px; color: var(--text); font-size: 24px; font-variant-numeric: tabular-nums; }
.metric-card small, .performance-panel small, .block { display: block; color: var(--text3); font-size: 11px; }
.performance-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.performance-panel { min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: 8px; }
.performance-panel header { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.performance-panel header span { color: var(--text3); font-size: 11px; }
.performance-panel dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0 0 10px; }
.performance-panel dl div { min-width: 0; padding: 9px; background: var(--surface2); }
.performance-panel dt { color: var(--text3); font-size: 11px; }
.performance-panel dd { margin: 5px 0 0; overflow-wrap: anywhere; color: var(--text); font-size: 15px; font-weight: 600; }
.cost-panel dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.table-wrap { width: 100%; min-width: 0; overflow-x: auto; }
.participant-form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: 8px; margin-bottom: 14px; }
@media (max-width: 1180px) { .metric-grid { grid-template-columns: repeat(3, minmax(125px, 1fr)); } .performance-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .toolbar-band, .metric-section, .batch-section { padding: 12px; } .filter-control, .date-control { width: 100%; } .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .metric-card strong { font-size: 20px; } .disclosure-band { align-items: flex-start; flex-direction: column; } .participant-form { grid-template-columns: 1fr; } }
</style>
