<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.statisticsDate" class="search-input" placeholder="统计日期 如 2026-05-30" @keyup.enter="reload" />
      <select v-model="searchForm.dataType" class="search-select" @change="reload">
        <option value="">全部</option>
        <option :value="1">销售金额</option>
        <option :value="2">订单数量</option>
        <option :value="3">退款金额</option>
        <option :value="4">退款数量</option>
      </select>
    </div>
    <button type="button" class="m-wide-btn" @click="syncStatistics">手动同步统计</button>

    <div v-if="list.length" class="m-list">
      <div v-for="(row, i) in list" :key="i" class="glass-card stat-row">
        <div class="stat-left">
          <span class="stat-date">{{ row.statisticsDate }}</span>
          <span class="stat-type">{{ typeLabel(row.dataType) }}</span>
        </div>
        <span class="stat-val">{{ row.dataValue }}</span>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无统计数据</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ statisticsDate: '', dataType: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

const TYPES = { 1: '销售金额', 2: '订单数量', 3: '退款金额', 4: '退款数量' }
const typeLabel = (t) => TYPES[t] || t

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
    if (searchForm.statisticsDate) params.statisticsDate = searchForm.statisticsDate
    if (searchForm.dataType !== '') params.dataType = searchForm.dataType
    const result = await proxy.Request({ url: proxy.Api.statisticsInfoLoadList, params, showLoading: false })
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

const syncStatistics = () => {
  proxy.Confirm({
    message: '将重新计算并写入统计数据，确定继续？',
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.toolStatistics, showLoading: true })
      if (!result) return
      proxy.Message.success('同步成功')
      reload()
    }
  })
}

onMounted(() => {
  loadList(true)
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadList()
    },
    { rootMargin: '0px 0px 300px 0px' }
  )
  if (sentinel.value) observer.observe(sentinel.value)
})

onUnmounted(() => {
  observer && observer.disconnect()
  observer = null
})
</script>

<style lang="scss" scoped>
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  border-radius: 8px;

  .search-input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    font-size: 14px;
    color: var(--m-ink);
    outline: none;

    &::placeholder {
      color: var(--m-ink-3);
    }
  }

  .search-select {
    flex-shrink: 0;
    border: none;
    background: rgba(255, 255, 255, 0.5);
    border-radius: 8px;
    padding: 4px 6px;
    font-size: 13px;
    color: var(--m-ink-2);
    outline: none;
  }
}

.m-wide-btn {
  height: 40px;
  border: 1px solid rgba(37, 99, 235, 0.32);
  border-radius: 8px;
  background: var(--m-gold-soft);
  color: #8a6d2c;
  font-size: 14px;
  cursor: pointer;
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stat-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;

  .stat-left {
    display: flex;
    flex-direction: column;
    gap: 2px;

    .stat-date {
      font-size: 13px;
      color: var(--m-ink);
    }

    .stat-type {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .stat-val {
    font-size: 17px;
    font-weight: 700;
    color: var(--m-ink);
  }
}

.m-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  font-size: 12px;
  color: var(--m-ink-3);
}

.m-empty-tip {
  margin: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--m-ink-3);
}
</style>
