<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.reasonFuzzy" class="search-input" placeholder="举报理由" @keyup.enter="reload" />
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option :value="undefined">全部状态</option>
        <option :value="0">待处理</option>
        <option :value="1">已处理</option>
        <option :value="2">已驳回</option>
      </select>
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.reportId" class="glass-card rpt-card">
        <div class="rpt-head">
          <span class="rpt-reason">{{ row.reason }}</span>
          <span class="rpt-status" :class="statusClass(row.status)">
            {{ statusLabel(row.status) }}
          </span>
        </div>
        <p class="rpt-order">订单号：{{ row.orderId }}</p>
        <p v-if="row.detail" class="rpt-detail">{{ row.detail }}</p>
        <p v-if="row.commentSnapshot" class="rpt-snapshot">"{{ row.commentSnapshot }}"</p>
        <p class="rpt-time">{{ row.reportTime }}</p>
        <p v-if="row.handleRemark" class="rpt-remark">
          <span class="tag">处理</span>{{ row.handleRemark }}
        </p>
        <div class="rpt-ops">
          <button
            v-if="row.status === 0"
            type="button"
            class="op-btn primary"
            @click="handleHandler(row)"
          >
            处理
          </button>
          <button type="button" class="op-btn danger" @click="del(row)">删除</button>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无举报记录</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <HandleReport ref="handleRef" @reload="reload"></HandleReport>
  </div>
</template>

<script setup>
import HandleReport from '@/views/order/HandleReport.vue'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ reasonFuzzy: '', status: undefined })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

const statusLabel = (s) => {
  if (s === 1) return '已处理'
  if (s === 2) return '已驳回'
  return '待处理'
}

const statusClass = (s) => {
  if (s === 1) return 'done'
  if (s === 2) return 'reject'
  return 'pending'
}

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
    const params = { pageNo: next, pageSize: 8 }
    if (searchForm.reasonFuzzy) params.reasonFuzzy = searchForm.reasonFuzzy
    if (searchForm.status !== undefined && searchForm.status !== '') params.status = Number(searchForm.status)
    const result = await proxy.Request({ url: proxy.Api.loadCommentReport, params, showLoading: false })
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

const handleRef = ref()
const handleHandler = (row) => handleRef.value.show(row)

const del = (row) => {
  proxy.Confirm({
    message: '确定要删除该举报记录吗？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.deleteCommentReport,
        params: { reportId: row.reportId }
      })
      if (!result) return
      proxy.Message.success('操作成功')
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
    border: 1px solid rgba(120, 120, 128, 0.24);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.5);
    height: 30px;
    padding: 0 6px;
    font-size: 12px;
    color: var(--m-ink);
    cursor: pointer;
    outline: none;
  }
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rpt-card {
  padding: 12px 14px;

  .rpt-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .rpt-reason {
    font-size: 14px;
    font-weight: 600;
    color: var(--m-ink);
  }

  .rpt-status {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 8px;
    font-weight: 500;

    &.pending {
      background: rgba(255, 149, 0, 0.12);
      color: #b87000;
    }

    &.done {
      background: rgba(52, 199, 89, 0.12);
      color: #1a7a3a;
    }

    &.reject {
      background: rgba(120, 120, 128, 0.12);
      color: var(--m-ink-3);
    }
  }

  .rpt-order {
    margin: 8px 0 4px;
    font-size: 12px;
    color: var(--m-ink-3);
  }

  .rpt-detail {
    margin: 0 0 4px;
    font-size: 13px;
    color: var(--m-ink-2);
  }

  .rpt-snapshot {
    margin: 6px 0;
    padding: 6px 10px;
    border-radius: 8px;
    background: rgba(120, 120, 128, 0.06);
    font-size: 12px;
    color: var(--m-ink-2);
    line-height: 1.45;
  }

  .rpt-time {
    margin: 4px 0 0;
    font-size: 11px;
    color: var(--m-ink-3);
  }

  .rpt-remark {
    margin: 8px 0 0;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(120, 120, 128, 0.08);
    font-size: 13px;
    color: var(--m-ink-2);

    .tag {
      display: inline-block;
      margin-right: 6px;
      padding: 0 6px;
      border-radius: 6px;
      background: var(--m-gold-soft);
      color: #1d4ed8;
      font-size: 11px;
    }
  }

  .rpt-ops {
    display: flex;
    gap: 8px;
    margin-top: 10px;

    .op-btn {
      flex: 1;
      height: 32px;
      border: 1px solid rgba(120, 120, 128, 0.24);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.5);
      color: var(--m-ink-2);
      font-size: 12px;
      cursor: pointer;

      &.primary {
        color: var(--m-blue);
        border-color: rgba(0, 113, 227, 0.3);
      }

      &.danger {
        color: var(--m-danger);
        border-color: rgba(255, 59, 48, 0.3);
      }
    }
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
