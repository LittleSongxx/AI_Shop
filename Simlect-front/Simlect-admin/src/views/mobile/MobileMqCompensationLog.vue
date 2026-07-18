<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input
        v-model="searchForm.idempotencyKeyFuzzy"
        class="search-input"
        placeholder="幂等键模糊搜索"
        @keyup.enter="reload"
      />
      <select v-model="searchForm.bizScene" class="search-select" @change="reload">
        <option value="">全部场景</option>
        <option value="RAG">RAG</option>
        <option value="NOTIFY">通知</option>
        <option value="BROWSE">足迹</option>
        <option value="SIGN">签到</option>
        <option value="PAY">订单</option>
        <option value="CONSUME">消费失败</option>
        <option value="OTHER">其他</option>
      </select>
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option :value="''">全部状态</option>
        <option :value="0">待处理</option>
        <option :value="1">处理中</option>
        <option :value="2">已重放成功</option>
        <option :value="3">重放失败</option>
        <option :value="4">已忽略</option>
      </select>
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.logId" class="glass-card log-row" @click="openDetail(row)">
        <div class="log-head">
          <span class="log-id">#{{ row.logId }}</span>
          <span class="log-scene">{{ row.bizScene || '—' }}</span>
          <span class="log-status" :class="'st-' + row.status">{{ statusLabel(row.status) }}</span>
        </div>
        <p class="log-key">{{ row.idempotencyKey }}</p>
        <p class="log-err">{{ row.errorMessage || '—' }}</p>
        <p class="log-meta">{{ row.createTime }} · 重试 {{ row.retryCount ?? 0 }}</p>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无补偿日志</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <div v-if="detailVisible" class="m-sheet-mask" @click.self="detailVisible = false">
      <div class="m-sheet glass-card">
        <h3 class="sheet-title">MQ 补偿 #{{ currentRow?.logId }}</h3>
        <div v-if="currentRow" class="sheet-body">
          <p><strong>场景</strong> {{ currentRow.bizScene }}</p>
          <p><strong>幂等键</strong> {{ currentRow.idempotencyKey }}</p>
          <p><strong>路由键</strong> {{ currentRow.routingKey }}</p>
          <p><strong>失败原因</strong> {{ currentRow.errorMessage || '—' }}</p>
          <pre class="payload-pre">{{ currentRow.payloadJson || '—' }}</pre>
          <label class="m-label">处理状态</label>
          <select v-model="handleForm.status" class="m-select">
            <option :value="0">待处理</option>
            <option :value="1">处理中</option>
            <option :value="2">已重放成功</option>
            <option :value="3">重放失败</option>
            <option :value="4">已忽略</option>
          </select>
          <label class="m-label">备注</label>
          <textarea v-model="handleForm.handleRemark" class="m-textarea" rows="3" maxlength="512" />
        </div>
        <div class="sheet-ops">
          <button type="button" class="op-btn" @click="detailVisible = false">关闭</button>
          <button type="button" class="op-btn warn" :disabled="replaying" @click="doReplay">重放</button>
          <button type="button" class="op-btn primary" :disabled="saving" @click="saveStatus">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ idempotencyKeyFuzzy: '', bizScene: '', status: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
const detailVisible = ref(false)
const currentRow = ref(null)
const saving = ref(false)
const replaying = ref(false)
const handleForm = reactive({ status: 0, handleRemark: '' })
let observer = null

const STATUS = { 0: '待处理', 1: '处理中', 2: '已重放', 3: '重放失败', 4: '已忽略' }
const statusLabel = (s) => STATUS[s] ?? s

const loadList = async (reset = false) => {
  if (loading.value) return
  if (reset) {
    pageNo.value = 0
    pageTotal.value = 1
    finished.value = false
    list.value = []
  }
  if (finished.value) return
  loading.value = true
  try {
    const next = pageNo.value + 1
    const params = { pageNo: next, pageSize: 15 }
    if (searchForm.idempotencyKeyFuzzy) params.idempotencyKeyFuzzy = searchForm.idempotencyKeyFuzzy
    if (searchForm.bizScene) params.bizScene = searchForm.bizScene
    if (searchForm.status !== '') params.status = searchForm.status
    const result = await proxy.Request({ url: proxy.Api.mqCompensationLogLoadList, params, showLoading: false })
    if (!result) return
    const data = result.data || {}
    const chunk = data.list || []
    list.value = next === 1 ? chunk : list.value.concat(chunk)
    pageNo.value = Number(data.pageNo) || next
    pageTotal.value = Number(data.pageTotal) || pageNo.value
    finished.value = pageNo.value >= pageTotal.value
  } finally {
    loading.value = false
  }
}

const reload = () => loadList(true)

const openDetail = (row) => {
  currentRow.value = row
  handleForm.status = row.status ?? 0
  handleForm.handleRemark = row.handleRemark || ''
  detailVisible.value = true
}

const saveStatus = async () => {
  if (!currentRow.value) return
  saving.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.mqCompensationLogUpdateStatus,
      params: {
        logId: currentRow.value.logId,
        status: handleForm.status,
        handleRemark: handleForm.handleRemark
      },
      showLoading: true
    })
    if (!result) return
    proxy.Message.success('已保存')
    detailVisible.value = false
    reload()
  } finally {
    saving.value = false
  }
}

const doReplay = async () => {
  if (!currentRow.value) return
  replaying.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.mqCompensationLogReplay,
      params: { logId: currentRow.value.logId },
      showLoading: true
    })
    if (!result) return
    proxy.Message.success('重放已提交')
    detailVisible.value = false
    reload()
  } finally {
    replaying.value = false
  }
}

onMounted(() => {
  loadList(true)
  observer = new IntersectionObserver((entries) => {
    if (entries[0]?.isIntersecting) loadList(false)
  }, { rootMargin: '120px' })
  if (sentinel.value) observer.observe(sentinel.value)
})

onUnmounted(() => {
  if (observer) observer.disconnect()
})
</script>

<style lang="scss" scoped>
.m-search {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.search-input,
.search-select {
  width: 100%;
  height: 40px;
  border-radius: 12px;
  border: 1px solid rgba(120, 120, 128, 0.2);
  padding: 0 12px;
  font-size: 14px;
  background: rgba(255, 255, 255, 0.6);
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.log-row {
  padding: 12px 14px;
  cursor: pointer;
}

.log-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.log-id {
  font-weight: 600;
  font-size: 13px;
}

.log-scene {
  font-size: 12px;
  color: var(--m-ink-2);
}

.log-status {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(120, 120, 128, 0.15);

  &.st-0 { color: #b56a00; background: rgba(255, 149, 0, 0.15); }
  &.st-2 { color: #248a3d; background: rgba(52, 199, 89, 0.15); }
  &.st-3 { color: var(--m-danger); background: rgba(255, 59, 48, 0.12); }
}

.log-key,
.log-err,
.log-meta {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--m-ink-2);
  word-break: break-all;
}

.log-err {
  color: var(--m-ink);
}

.m-sheet-mask {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: flex-end;
}

.m-sheet {
  width: 100%;
  max-height: 85vh;
  overflow: auto;
  border-radius: 16px 16px 0 0;
  padding: 16px;
}

.sheet-title {
  margin: 0 0 12px;
  font-size: 16px;
}

.sheet-body p {
  margin: 0 0 8px;
  font-size: 13px;
  word-break: break-all;
}

.payload-pre {
  margin: 8px 0;
  padding: 8px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.04);
  font-size: 11px;
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
}

.sheet-ops {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.op-btn {
  flex: 1;
  height: 42px;
  border-radius: 12px;
  border: 1px solid rgba(120, 120, 128, 0.25);
  background: #fff;
  font-size: 14px;

  &.primary {
    background: var(--m-ink);
    color: #fff;
    border: none;
  }

  &.warn {
    color: #b56a00;
    border-color: rgba(255, 149, 0, 0.4);
  }
}
</style>
