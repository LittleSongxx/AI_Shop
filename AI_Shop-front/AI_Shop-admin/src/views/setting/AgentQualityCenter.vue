<template>
  <div class="quality-center">
    <el-tabs v-model="activeTab" class="quality-tabs" @tab-change="onTabChange">
      <el-tab-pane label="Trace" name="trace">
        <div class="filter-row">
          <el-select v-model="traceFilters.status" clearable placeholder="运行状态" class="filter-control">
            <el-option label="运行中" value="RUNNING" />
            <el-option label="成功" value="SUCCEEDED" />
            <el-option label="失败" value="FAILED" />
            <el-option label="已降级" value="DEGRADED" />
            <el-option label="转人工" value="HANDOFF" />
            <el-option label="已取消" value="CANCELLED" />
          </el-select>
          <el-input v-model="traceFilters.intent" clearable placeholder="意图" class="filter-control" />
          <el-input v-model="traceFilters.userId" clearable placeholder="用户 ID" class="filter-control" />
          <el-input v-model="traceFilters.outcome" clearable placeholder="终态" class="filter-control" />
          <el-button type="primary" :loading="traceLoading" @click="loadTraces(1)">查询</el-button>
        </div>
        <div class="table-wrap">
          <el-table :data="tracePage.list" v-loading="traceLoading" stripe @row-dblclick="openTrace">
            <el-table-column label="Run ID" prop="runId" min-width="240" show-overflow-tooltip />
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="runStatusType(row.status)">{{ row.status || '—' }}</el-tag></template></el-table-column>
            <el-table-column label="场景 / 意图" min-width="190"><template #default="{ row }"><b>{{ row.scenario || '—' }}</b><small class="block">{{ row.intent || '—' }}</small></template></el-table-column>
            <el-table-column label="终态" prop="outcome" width="130" show-overflow-tooltip />
            <el-table-column label="耗时" width="100"><template #default="{ row }">{{ formatDuration(row.latencyMs) }}</template></el-table-column>
            <el-table-column label="Token" width="105"><template #default="{ row }">{{ Number(row.inputTokens || 0) + Number(row.outputTokens || 0) }}</template></el-table-column>
            <el-table-column label="数据审核" width="120"><template #default="{ row }"><el-tag size="small" :type="datasetType(row.datasetEligible)">{{ row.datasetEligible || 'UNREVIEWED' }}</el-tag></template></el-table-column>
            <el-table-column label="开始时间" prop="startedAt" min-width="180" />
            <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openTrace(row)">详情</el-button></template></el-table-column>
          </el-table>
        </div>
        <Pagination :page="tracePage" @change="loadTraces" />
      </el-tab-pane>

      <el-tab-pane label="Badcase" name="badcase">
        <div class="filter-row">
          <el-select v-model="badcaseStatus" clearable placeholder="全部状态" class="filter-control">
            <el-option v-for="status in badcaseStatuses" :key="status" :label="status" :value="status" />
          </el-select>
          <el-button type="primary" :loading="badcaseLoading" @click="loadBadcases(1)">查询</el-button>
          <el-button @click="openRegressions">回归 Case</el-button>
        </div>
        <div class="table-wrap">
          <el-table :data="badcasePage.list" v-loading="badcaseLoading" stripe>
            <el-table-column label="ID" prop="candidateId" width="80" />
            <el-table-column label="状态" width="135"><template #default="{ row }"><el-tag :type="badcaseStatusType(row.status)">{{ row.status }}</el-tag></template></el-table-column>
            <el-table-column label="来源 / 严重度" width="150"><template #default="{ row }">{{ row.source || '—' }}<small class="block">{{ row.severity || '—' }} · {{ row.occurrenceCount || 1 }} 次</small></template></el-table-column>
            <el-table-column label="原因" prop="reason" min-width="190" show-overflow-tooltip />
            <el-table-column label="用户问题" prop="userMessage" min-width="220" show-overflow-tooltip />
            <el-table-column label="标签" min-width="150"><template #default="{ row }"><el-tag v-for="label in row.labels || []" :key="label" size="small" class="label-tag">{{ label }}</el-tag><span v-if="!(row.labels || []).length">—</span></template></el-table-column>
            <el-table-column label="Owner / 版本" width="150"><template #default="{ row }">{{ row.owner || '—' }}<small class="block">{{ row.fixVersion || '—' }}</small></template></el-table-column>
            <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="terminalBadcase(row.status)" @click="openBadcase(row)">处理</el-button></template></el-table-column>
          </el-table>
        </div>
        <Pagination :page="badcasePage" @change="loadBadcases" />
      </el-tab-pane>

      <el-tab-pane label="工单" name="support">
        <div class="filter-row">
          <el-select v-model="supportFilters.status" clearable placeholder="工单状态" class="filter-control">
            <el-option label="待处理" value="OPEN" />
            <el-option label="处理中" value="IN_PROGRESS" />
            <el-option label="已解决" value="RESOLVED" />
            <el-option label="已取消" value="CANCELLED" />
          </el-select>
          <el-input v-model="supportFilters.userId" clearable placeholder="用户 ID" class="filter-control" />
          <el-button type="primary" :loading="supportLoading" @click="loadSupportCases(1)">查询</el-button>
        </div>
        <div class="table-wrap">
          <el-table :data="supportPage.list" v-loading="supportLoading" stripe>
            <el-table-column label="工单号" prop="caseNo" min-width="185" />
            <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="supportStatusType(row.status)">{{ supportStatusText(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="类别" prop="categoryLabel" width="120" />
            <el-table-column label="优先级" prop="priority" width="95" />
            <el-table-column label="用户 / 订单" min-width="170"><template #default="{ row }">{{ row.userId || '—' }}<small class="block">{{ row.orderId || '未关联订单' }}</small></template></el-table-column>
            <el-table-column label="描述" prop="description" min-width="230" show-overflow-tooltip />
            <el-table-column label="处理客服" prop="assignedAdmin" width="125" />
            <el-table-column label="更新时间" prop="updatedAt" min-width="180" />
            <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openSupportCase(row)">处理</el-button></template></el-table-column>
          </el-table>
        </div>
        <Pagination :page="supportPage" @change="loadSupportCases" />
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="traceDrawer.show" title="Episode Trace" :size="drawerSize" destroy-on-close>
      <template v-if="traceDrawer.detail">
        <div class="detail-summary">
          <div><span>Run ID</span><b>{{ traceDrawer.detail.runId }}</b></div>
          <div><span>Trace ID</span><b>{{ traceDrawer.detail.traceId || '—' }}</b></div>
          <div><span>终态</span><b>{{ traceDrawer.detail.outcome || traceDrawer.detail.status || '—' }}</b></div>
          <div><span>模型 / Token</span><b>{{ traceDrawer.detail.modelName || '—' }} · {{ tokenTotal(traceDrawer.detail) }}</b></div>
          <div><span>数据审核</span><b>{{ traceDrawer.detail.datasetEligible || 'UNREVIEWED' }} · {{ traceDrawer.detail.datasetReviewedBy || '未审核' }}</b></div>
          <div><span>训练资格判定</span><b>{{ traceDrawer.detail.episodeEvaluation?.verdict || '—' }}</b></div>
        </div>
        <div class="drawer-actions top-actions">
          <a v-if="traceDrawer.detail.tempoTraceUrl" :href="traceDrawer.detail.tempoTraceUrl" target="_blank" rel="noopener noreferrer" class="tempo-link">在 Tempo 查看</a>
        </div>
        <section class="episode-review">
          <div class="review-heading"><h3>人工数据审核</h3><el-tag :type="traceDrawer.detail.episodeEvaluation?.reviewEligible ? 'success' : 'warning'">{{ traceDrawer.detail.episodeEvaluation?.reviewEligible ? '事实完整' : '暂不可批准' }}</el-tag></div>
          <p>{{ traceDrawer.detail.episodeEvaluation?.verdict || '尚无资格判定' }}<template v-if="traceDrawer.detail.datasetReviewedAt"> · {{ traceDrawer.detail.datasetReviewedAt }}</template></p>
          <el-input v-model="episodeReviewNote" type="textarea" :rows="2" maxlength="1000" placeholder="审核备注" />
          <div class="drawer-actions">
            <el-button :loading="episodeReviewing" type="danger" plain @click="reviewEpisode('REJECTED')">拒绝</el-button>
            <el-button :loading="episodeReviewing" type="success" :disabled="!traceDrawer.detail.episodeEvaluation?.reviewEligible" @click="reviewEpisode('APPROVED')">批准为训练候选</el-button>
          </div>
        </section>
        <section class="quality-json"><h3>质量与事实 Reward Signals</h3><pre>{{ pretty({ quality: traceDrawer.detail.quality, rewardSignals: traceDrawer.detail.rewardSignals, experiment: traceDrawer.detail.experiment }) }}</pre></section>
        <section class="waterfall">
          <article v-for="(step, index) in traceDrawer.detail.steps || []" :key="step.stepId || index" class="trace-step">
            <span class="step-line" aria-hidden="true" />
            <span class="step-index">{{ index + 1 }}</span>
            <div class="step-body">
              <header><b>{{ step.nodeName || step.toolName || step.eventType }}</b><el-tag size="small" :type="step.status === 'ERROR' ? 'danger' : 'info'">{{ step.status || step.eventType }}</el-tag><span>{{ formatDuration(step.latencyMs) }}</span></header>
              <p>{{ step.eventType }} · {{ step.occurredAt || '—' }}<template v-if="step.spanId"> · span {{ step.spanId }}</template></p>
              <details v-if="step.input || step.output || step.errorMessage"><summary>脱敏 observation / action / result</summary><pre>{{ pretty({ input: step.input, output: step.output, errorCode: step.errorCode, errorMessage: step.errorMessage }) }}</pre></details>
            </div>
          </article>
        </section>
      </template>
    </el-drawer>

    <el-drawer v-model="badcaseDrawer.show" title="Badcase 处理" :size="drawerSize" destroy-on-close>
      <el-form v-if="badcaseDrawer.row" label-position="top" :model="badcaseReview">
        <div class="case-context"><b>用户问题</b><p>{{ badcaseDrawer.row.userMessage || '—' }}</p><b>AI 回复</b><p>{{ badcaseDrawer.row.assistantMessage || '—' }}</p><b>原因</b><p>{{ badcaseDrawer.row.reason || '—' }}</p></div>
        <el-form-item label="目标状态" required><el-select v-model="badcaseReview.status" class="full"><el-option v-for="status in availableBadcaseTransitions" :key="status" :label="status" :value="status" /></el-select></el-form-item>
        <el-form-item label="标签（逗号分隔）"><el-input v-model="badcaseReview.labels" placeholder="grounding, tool_error" /></el-form-item>
        <el-form-item label="Owner"><el-input v-model="badcaseReview.owner" placeholder="进入 FIXING 前必填" /></el-form-item>
        <el-form-item label="修复版本"><el-input v-model="badcaseReview.fixVersion" placeholder="例如 v1.8.0" /></el-form-item>
        <el-form-item label="审核备注"><el-input v-model="badcaseReview.remark" type="textarea" :rows="3" maxlength="500" /></el-form-item>
        <template v-if="badcaseReview.status === 'REGRESSION_ADDED'">
          <el-divider content-position="left">回归 Case</el-divider>
          <el-form-item label="Case 名称" required><el-input v-model="badcaseReview.regressionName" /></el-form-item>
          <el-form-item label="场景"><el-input v-model="badcaseReview.regressionScenario" /></el-form-item>
          <el-form-item label="输入 JSON" required><el-input v-model="badcaseReview.regressionInput" type="textarea" :rows="5" /></el-form-item>
          <el-form-item label="期望 JSON" required><el-input v-model="badcaseReview.regressionExpected" type="textarea" :rows="5" /></el-form-item>
        </template>
        <div class="drawer-actions"><el-button @click="badcaseDrawer.show = false">取消</el-button><el-button type="primary" :loading="badcaseReviewing" @click="reviewBadcase">提交审核</el-button></div>
      </el-form>
    </el-drawer>

    <el-drawer v-model="regressionDrawer.show" title="回归 Case" :size="drawerSize">
      <div class="drawer-actions regression-actions"><el-button type="primary" :icon="VideoPlay" :loading="regressionDrawer.running" @click="runRegressions()">运行全部 ACTIVE Case</el-button></div>
      <el-table :data="regressionDrawer.page.list" v-loading="regressionDrawer.loading" stripe>
        <el-table-column label="ID" prop="caseId" width="70" />
        <el-table-column label="名称" prop="name" min-width="180" />
        <el-table-column label="场景" prop="scenario" min-width="120" />
        <el-table-column label="状态" prop="status" width="95" />
        <el-table-column label="最近结果" width="105"><template #default="{ row }"><el-tag :type="row.lastResult === 'PASS' ? 'success' : row.lastResult === 'FAIL' ? 'danger' : 'info'">{{ row.lastResult || '未运行' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="85" fixed="right"><template #default="{ row }"><el-button link type="primary" :icon="VideoPlay" :loading="regressionDrawer.runningCaseId === row.caseId" @click="runRegressions(row.caseId)">运行</el-button></template></el-table-column>
      </el-table>
    </el-drawer>

    <el-drawer v-model="supportDrawer.show" title="工单处理" :size="drawerSize" destroy-on-close>
      <template v-if="supportDrawer.detail">
        <div class="detail-summary">
          <div><span>工单号</span><b>{{ supportDrawer.detail.caseNo || supportDrawer.detail.caseId }}</b></div>
          <div><span>状态</span><b>{{ supportStatusText(supportDrawer.detail.status) }}</b></div>
          <div><span>类别 / 优先级</span><b>{{ supportDrawer.detail.categoryLabel || supportDrawer.detail.category }} · {{ supportDrawer.detail.priority }}</b></div>
          <div><span>关联订单</span><b>{{ supportDrawer.detail.orderId || '未关联' }}</b></div>
        </div>
        <section class="case-context"><b>问题描述</b><p>{{ supportDrawer.detail.description }}</p><b>人工会话</b><p>{{ supportDrawer.detail.supportSessionId || '未关联' }}</p></section>
        <section v-if="supportEvidencePath || supportDrawer.detail.evidence?.vlmDescription" class="support-evidence"><img v-if="supportEvidencePath" :src="supportEvidencePath" alt="售后图片证据" /><div><p>审核：{{ supportDrawer.detail.evidence?.moderationStatus || '已记录' }}</p><p v-if="supportDrawer.detail.evidence?.vlmStatus">VLM：{{ supportDrawer.detail.evidence.vlmStatus }}</p><p v-if="supportDrawer.detail.evidence?.vlmDescription">{{ supportDrawer.detail.evidence.vlmDescription }}</p></div></section>
        <div v-if="supportDrawer.detail.status === 'OPEN'" class="drawer-actions"><el-button type="primary" :loading="supportMutating" @click="claimSupportCase">认领工单</el-button></div>
        <div v-else-if="supportDrawer.detail.status === 'IN_PROGRESS'" class="drawer-actions"><el-button :loading="supportMutating" @click="markSupportInProgress">标记处理中</el-button></div>
        <el-form v-if="supportDrawer.detail.status === 'IN_PROGRESS'" label-position="top" :model="resolutionForm" class="resolution-form">
          <el-divider content-position="left">解决工单</el-divider>
          <el-form-item label="解决码" required><el-input v-model="resolutionForm.resolutionCode" maxlength="64" placeholder="例如 REFUND_COMPLETED" /></el-form-item>
          <el-form-item label="根因" required><el-input v-model="resolutionForm.rootCause" maxlength="500" /></el-form-item>
          <el-form-item label="处理摘要" required><el-input v-model="resolutionForm.resolutionSummary" type="textarea" :rows="4" maxlength="2000" /></el-form-item>
          <el-form-item label="关联人工会话"><el-input v-model="resolutionForm.supportSessionId" placeholder="可选" /></el-form-item>
          <div class="drawer-actions"><el-button type="success" :loading="supportMutating" @click="resolveSupportCase">提交解决结果</el-button></div>
        </el-form>
        <section v-if="supportDrawer.detail.status === 'RESOLVED'" class="case-context"><b>解决码</b><p>{{ supportDrawer.detail.resolutionCode }}</p><b>根因</b><p>{{ supportDrawer.detail.rootCause }}</p><b>处理摘要</b><p>{{ supportDrawer.detail.resolutionSummary }}</p></section>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, defineComponent, getCurrentInstance, h, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElPagination } from 'element-plus'
import { VideoPlay } from '@element-plus/icons-vue'

const { proxy } = getCurrentInstance()
const activeTab = ref('trace')
const drawerSize = computed(() => window.innerWidth < 760 ? '94%' : '720px')
const emptyPage = () => ({ list: [], totalCount: 0, pageNo: 1, pageSize: 20 })
const normalizePage = (data) => ({ ...emptyPage(), ...(data || {}), list: Array.isArray(data?.list) ? data.list : [] })
const request = async (url, params = {}) => {
  const result = await proxy.Request({ url, params, showLoading: false })
  return result?.data
}

const Pagination = defineComponent({
  props: { page: { type: Object, required: true } },
  emits: ['change'],
  setup(props, { emit }) {
    return () => h('div', { class: 'pagination-row' }, [h(ElPagination, {
      currentPage: Number(props.page.pageNo || 1), pageSize: Number(props.page.pageSize || 20), total: Number(props.page.totalCount || 0),
      layout: 'prev, pager, next, total', background: true,
      'onUpdate:currentPage': (value) => emit('change', value)
    })])
  }
})

const traceFilters = reactive({ status: '', intent: '', userId: '', outcome: '' })
const tracePage = ref(emptyPage())
const traceLoading = ref(false)
const traceDrawer = reactive({ show: false, detail: null })
const episodeReviewing = ref(false)
const episodeReviewNote = ref('')
const loadTraces = async (pageNo = 1) => {
  traceLoading.value = true
  try { tracePage.value = normalizePage(await request(proxy.Api.agentTraceRuns, { ...traceFilters, pageNo, pageSize: 20 })) } finally { traceLoading.value = false }
}
const openTrace = async (row) => {
  const detail = await request(proxy.Api.agentTraceDetail, { runId: row.runId })
  if (!detail) return
  traceDrawer.detail = detail
  episodeReviewNote.value = detail.datasetReviewNote || ''
  traceDrawer.show = true
}
const reviewEpisode = async (datasetEligible) => {
  episodeReviewing.value = true
  try {
    const detail = await request(proxy.Api.agentReviewEpisode, { runId: traceDrawer.detail.runId, datasetEligible, note: episodeReviewNote.value })
    if (!detail) return
    traceDrawer.detail = detail
    episodeReviewNote.value = detail.datasetReviewNote || ''
    ElMessage.success(datasetEligible === 'APPROVED' ? 'Episode 已批准为训练候选' : 'Episode 已拒绝')
    await loadTraces(tracePage.value.pageNo)
  } finally { episodeReviewing.value = false }
}

const badcaseStatuses = ['NEW', 'TRIAGED', 'LABELED', 'FIXING', 'REGRESSION_ADDED', 'VERIFIED', 'CLOSED', 'IGNORED', 'NOT_A_BUG']
const badcaseTransitions = { NEW: ['TRIAGED', 'IGNORED', 'NOT_A_BUG'], TRIAGED: ['LABELED', 'IGNORED', 'NOT_A_BUG'], LABELED: ['FIXING', 'REGRESSION_ADDED', 'IGNORED', 'NOT_A_BUG'], FIXING: ['REGRESSION_ADDED', 'IGNORED', 'NOT_A_BUG'], REGRESSION_ADDED: ['VERIFIED', 'IGNORED', 'NOT_A_BUG'], VERIFIED: ['CLOSED'] }
const badcaseStatus = ref('NEW')
const badcasePage = ref(emptyPage())
const badcaseLoading = ref(false)
const badcaseReviewing = ref(false)
const badcaseDrawer = reactive({ show: false, row: null })
const badcaseReview = reactive({ status: '', labels: '', owner: '', fixVersion: '', remark: '', regressionName: '', regressionScenario: '', regressionInput: '{}', regressionExpected: '{}' })
const availableBadcaseTransitions = computed(() => badcaseTransitions[badcaseDrawer.row?.status] || [])
const loadBadcases = async (pageNo = 1) => {
  badcaseLoading.value = true
  try { badcasePage.value = normalizePage(await request(proxy.Api.agentBadcases, { status: badcaseStatus.value, pageNo, pageSize: 20 })) } finally { badcaseLoading.value = false }
}
const openBadcase = (row) => {
  badcaseDrawer.row = row
  Object.assign(badcaseReview, { status: (badcaseTransitions[row.status] || [])[0] || '', labels: (row.labels || []).join(', '), owner: row.owner || '', fixVersion: row.fixVersion || '', remark: row.reviewRemark || '', regressionName: '', regressionScenario: row.intent || '', regressionInput: JSON.stringify({ messageId: row.messageId, userMessage: row.userMessage }, null, 2), regressionExpected: JSON.stringify({ intent: row.intent || 'REPLACE_WITH_EXPECTED_INTENT' }, null, 2) })
  badcaseDrawer.show = true
}
const parseJson = (value, label) => { try { const parsed = JSON.parse(value); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(); return parsed } catch { throw new Error(`${label}必须是 JSON 对象`) } }
const reviewBadcase = async () => {
  if (!badcaseReview.status) return ElMessage.warning('请选择目标状态')
  if (badcaseReview.status === 'LABELED' && !badcaseReview.labels.trim()) return ElMessage.warning('进入 LABELED 前至少填写一个标签')
  if (badcaseReview.status === 'FIXING' && !badcaseReview.owner.trim()) return ElMessage.warning('进入 FIXING 前必须指定 Owner')
  let regression
  try {
    if (badcaseReview.status === 'REGRESSION_ADDED') {
      if (!badcaseReview.regressionName.trim()) return ElMessage.warning('请填写回归 Case 名称')
      regression = { name: badcaseReview.regressionName.trim(), scenario: badcaseReview.regressionScenario.trim(), input: parseJson(badcaseReview.regressionInput, '输入'), expected: parseJson(badcaseReview.regressionExpected, '期望') }
    }
  } catch (error) { return ElMessage.error(error.message) }
  badcaseReviewing.value = true
  try {
    const data = await request(proxy.Api.agentReviewBadcase, { candidateId: badcaseDrawer.row.candidateId, status: badcaseReview.status, labels: JSON.stringify(badcaseReview.labels.split(/[,，]/).map(v => v.trim()).filter(Boolean)), owner: badcaseReview.owner, fixVersion: badcaseReview.fixVersion, remark: badcaseReview.remark, regression: regression ? JSON.stringify(regression) : '' })
    if (!data) return
    ElMessage.success('Badcase 状态已更新')
    badcaseDrawer.show = false
    await loadBadcases(badcasePage.value.pageNo)
  } finally { badcaseReviewing.value = false }
}
const regressionDrawer = reactive({ show: false, loading: false, running: false, runningCaseId: null, page: emptyPage() })
const openRegressions = async () => {
  regressionDrawer.show = true; regressionDrawer.loading = true
  try { regressionDrawer.page = normalizePage(await request(proxy.Api.agentRegressionCases, { pageNo: 1, pageSize: 50, status: '' })) } finally { regressionDrawer.loading = false }
}
const runRegressions = async (caseId) => {
  regressionDrawer.running = !caseId
  regressionDrawer.runningCaseId = caseId || null
  try {
    const result = await request(proxy.Api.agentRunRegressionCases, caseId ? { caseId } : {})
    if (!result) return
    if (result.failed || result.errors) ElMessage.warning(`回归完成：${result.passed} 通过，${result.failed} 失败，${result.errors} 异常`)
    else ElMessage.success(`回归完成：${result.passed} 个 Case 全部通过`)
    await openRegressions()
  } finally {
    regressionDrawer.running = false
    regressionDrawer.runningCaseId = null
  }
}

const supportFilters = reactive({ status: '', userId: '' })
const supportPage = ref(emptyPage())
const supportLoading = ref(false)
const supportMutating = ref(false)
const supportDrawer = reactive({ show: false, detail: null })
const resolutionForm = reactive({ resolutionCode: '', rootCause: '', resolutionSummary: '', supportSessionId: '' })
const loadSupportCases = async (pageNo = 1) => {
  supportLoading.value = true
  try { supportPage.value = normalizePage(await request(proxy.Api.agentSupportCases, { ...supportFilters, pageNo, pageSize: 20 })) } finally { supportLoading.value = false }
}
const openSupportCase = async (row) => {
  const detail = await request(proxy.Api.agentSupportCaseDetail, { caseId: row.caseNo || row.caseId })
  if (!detail) return
  supportDrawer.detail = detail
  Object.assign(resolutionForm, { resolutionCode: detail.resolutionCode || '', rootCause: detail.rootCause || '', resolutionSummary: detail.resolutionSummary || '', supportSessionId: detail.supportSessionId || '' })
  supportDrawer.show = true
}
const mutateSupport = async (url, params) => {
  supportMutating.value = true
  try {
    const detail = await request(url, params)
    if (!detail) return false
    supportDrawer.detail = detail
    await loadSupportCases(supportPage.value.pageNo)
    return true
  } finally { supportMutating.value = false }
}
const claimSupportCase = async () => { if (await mutateSupport(proxy.Api.agentSupportCaseClaim, { caseId: supportDrawer.detail.caseNo || supportDrawer.detail.caseId })) ElMessage.success('工单已认领') }
const markSupportInProgress = async () => { if (await mutateSupport(proxy.Api.agentSupportCaseInProgress, { caseId: supportDrawer.detail.caseNo || supportDrawer.detail.caseId })) ElMessage.success('工单已进入处理状态') }
const resolveSupportCase = async () => {
  if (!resolutionForm.resolutionCode.trim() || !resolutionForm.rootCause.trim() || !resolutionForm.resolutionSummary.trim()) return ElMessage.warning('解决码、根因和处理摘要均为必填项')
  const ok = await mutateSupport(proxy.Api.agentSupportCaseResolve, { caseId: supportDrawer.detail.caseNo || supportDrawer.detail.caseId, ...resolutionForm })
  if (ok) ElMessage.success('工单已解决，人工结果已回写 Episode')
}

const onTabChange = (name) => { if (name === 'trace' && !tracePage.value.list.length) loadTraces(); if (name === 'badcase' && !badcasePage.value.list.length) loadBadcases(); if (name === 'support' && !supportPage.value.list.length) loadSupportCases() }
const pretty = (value) => JSON.stringify(value || {}, null, 2)
const tokenTotal = (row) => Number(row?.inputTokens || 0) + Number(row?.outputTokens || 0)
const formatDuration = (value) => value == null ? '—' : Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(2)}s` : `${Number(value)}ms`
const runStatusType = (value) => value === 'FAILED' ? 'danger' : value === 'SUCCEEDED' ? 'success' : ['DEGRADED', 'HANDOFF'].includes(value) ? 'warning' : 'info'
const datasetType = (value) => value === 'APPROVED' ? 'success' : value === 'REJECTED' ? 'danger' : 'info'
const badcaseStatusType = (value) => ['CLOSED', 'VERIFIED'].includes(value) ? 'success' : ['IGNORED', 'NOT_A_BUG'].includes(value) ? 'info' : value === 'NEW' ? 'danger' : 'warning'
const terminalBadcase = (value) => ['CLOSED', 'IGNORED', 'NOT_A_BUG'].includes(value)
const supportStatusText = (value) => ({ OPEN: '待处理', IN_PROGRESS: '处理中', RESOLVED: '已解决', CANCELLED: '已取消' }[value] || value || '—')
const supportStatusType = (value) => value === 'RESOLVED' ? 'success' : value === 'OPEN' ? 'danger' : value === 'CANCELLED' ? 'info' : 'warning'
const supportEvidencePath = computed(() => supportDrawer.detail?.evidence?.path ? `/api/file/getResource?sourceName=${encodeURIComponent(supportDrawer.detail.evidence.path)}` : '')

onMounted(loadTraces)
</script>

<style lang="scss" scoped>
.quality-center { min-width: 0; }
.quality-tabs { min-width: 0; }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }
.filter-control { width: 170px; }
.table-wrap { width: 100%; min-width: 0; overflow-x: auto; }
.block { display: block; margin-top: 3px; color: var(--text3); font-size: 11px; }
.label-tag { margin: 2px 4px 2px 0; }
:deep(.pagination-row) { display: flex; justify-content: flex-end; padding-top: 14px; }
.detail-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.detail-summary div { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface2); }
.detail-summary span { color: var(--text3); font-size: 11px; }
.detail-summary b { overflow-wrap: anywhere; color: var(--text); font-size: 13px; }
.top-actions { margin: 12px 0; }
.tempo-link { color: var(--primary); font-size: 13px; text-decoration: none; }
.episode-review { margin: 14px 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.review-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.review-heading h3 { margin: 0; font-size: 14px; }
.episode-review > p { margin: 0 0 10px; color: var(--text2); font-size: 12px; }
.regression-actions { margin: 0 0 12px; }
.quality-json h3, .waterfall h3 { margin: 16px 0 8px; font-size: 14px; }
pre { max-width: 100%; margin: 8px 0 0; padding: 10px; overflow: auto; border-radius: 6px; background: #f5f7f8; color: #263238; font-size: 11px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.waterfall { margin-top: 16px; }
.trace-step { position: relative; display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 10px; padding-bottom: 14px; }
.step-line { position: absolute; top: 24px; bottom: 0; left: 12px; width: 1px; background: var(--border); }
.step-index { position: relative; z-index: 1; display: grid; place-items: center; width: 25px; height: 25px; border-radius: 50%; background: var(--primary); color: #fff; font-size: 11px; }
.step-body { min-width: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: 6px; }
.step-body header { display: flex; align-items: center; gap: 8px; }
.step-body header b { min-width: 0; margin-right: auto; overflow-wrap: anywhere; }
.step-body header > span { color: var(--text3); font-size: 11px; }
.step-body p, .case-context p, .support-evidence p { margin: 6px 0; color: var(--text2); font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.step-body summary { margin-top: 8px; color: var(--primary); font-size: 12px; cursor: pointer; }
.case-context { margin: 14px 0; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface2); }
.case-context b { font-size: 12px; }
.full { width: 100%; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.support-evidence { display: flex; align-items: flex-start; gap: 12px; margin: 14px 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.support-evidence img { width: 110px; height: 110px; flex: 0 0 auto; border-radius: 6px; object-fit: cover; }
.resolution-form { margin-top: 12px; }
@media (max-width: 720px) {
  .filter-control { width: min(100%, 210px); }
  .detail-summary { grid-template-columns: 1fr; }
  .quality-center { padding-bottom: 10px; }
  .support-evidence { flex-direction: column; }
}
</style>
