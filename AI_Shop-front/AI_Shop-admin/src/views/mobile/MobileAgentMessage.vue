<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <span class="iconfont icon-search search-icon"></span>
      <input
        v-model="userId"
        class="search-input"
        type="search"
        placeholder="按用户ID筛选"
        @keyup.enter="reload"
      />
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.messageId" class="glass-card m-msg">
        <div class="msg-head">
          <span class="msg-id">#{{ row.messageId }}</span>
          <span class="m-tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
          <span v-if="row.bizType" class="msg-biz">{{ row.bizType }}</span>
          <button type="button" class="msg-del" @click="delRow(row)">删除</button>
        </div>
        <p class="msg-user">{{ row.userMessage || '—' }}</p>
        <p class="msg-ai">{{ clip(row.assistantMessage) }}</p>
        <div class="msg-foot">
          <span>{{ row.userId }}</span>
          <span>{{ row.sendTime }}</span>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无对话记录</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>
  </div>
</template>

<script setup>
import { ref, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const userId = ref('')
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

const clip = (t) => (!t ? '—' : String(t).length > 140 ? String(t).slice(0, 140) + '…' : String(t))
const statusText = (s) => (s === 0 ? '已取消' : s === 1 ? '回答中' : '完成')
const statusClass = (s) => (s === 0 ? 'muted' : s === 1 ? 'gold' : 'green')

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
    const params = { pageNo: next, pageSize: 10 }
    if (userId.value) params.userId = userId.value
    const result = await proxy.Request({ url: proxy.Api.agentMessageLoadList, params, showLoading: false })
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

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除对话记录 #${row.messageId} 吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.agentMessageDelete,
        params: { messageId: row.messageId }
      })
      if (!result) return
      proxy.Message.success('已删除')
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

  .search-icon {
    font-size: 16px;
    color: var(--m-ink-3);
  }

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
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m-msg {
  padding: 12px 14px;

  .msg-head {
    display: flex;
    align-items: center;
    gap: 8px;

    .msg-id {
      font-size: 12px;
      color: var(--m-ink-3);
    }

    .msg-biz {
      font-size: 11px;
      color: var(--m-ink-2);
    }

    .msg-del {
      margin-left: auto;
      border: none;
      background: transparent;
      color: var(--m-danger);
      font-size: 12px;
      cursor: pointer;
    }
  }

  .msg-user {
    margin: 8px 0 4px;
    font-size: 13px;
    color: var(--m-ink);
    word-break: break-word;
  }

  .msg-ai {
    margin: 0;
    font-size: 13px;
    color: var(--m-ink-2);
    word-break: break-word;
  }

  .msg-foot {
    display: flex;
    justify-content: space-between;
    margin-top: 8px;
    font-size: 11px;
    color: var(--m-ink-3);
  }
}

.m-tag {
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 11px;

  &.green {
    background: rgba(52, 199, 89, 0.16);
    color: #1c8c3c;
  }

  &.gold {
    background: var(--m-gold-soft);
    color: #1d4ed8;
  }

  &.muted {
    background: rgba(120, 120, 128, 0.16);
    color: var(--m-ink-2);
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
