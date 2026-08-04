<template>
  <div class="m-user">
    <div class="m-search glass-card glass-strong">
      <span class="iconfont icon-search search-icon"></span>
      <input
        v-model="searchForm.nickNameFuzzy"
        class="search-input"
        type="search"
        placeholder="搜索用户昵称"
        @keyup.enter="reload"
      />
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option value="">全部</option>
        <option :value="1">正常</option>
        <option :value="0">禁用</option>
      </select>
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="u in list" :key="u.userId" class="m-user-card glass-card">
        <Avatar :avatar="u.avatar || undefined" :width="46"></Avatar>
        <div class="user-info">
          <div class="user-top">
            <span class="user-name">{{ u.nickName }}</span>
            <span class="user-sex">{{ SEX_MAP[u.sex] || '未知' }}</span>
            <span class="m-tag" :class="u.status == 1 ? 'green' : 'danger'">
              {{ u.status == 1 ? '正常' : '已禁用' }}
            </span>
          </div>
          <div class="user-id">ID：{{ u.userId }}</div>
          <div class="user-mail">{{ u.email || '未绑定邮箱' }}</div>
          <div class="user-time">加入 {{ u.joinTime }}</div>
        </div>
        <button type="button" class="user-op" :class="{ danger: u.status == 1 }" @click="changeStatus(u)">
          {{ u.status == 0 ? '启用' : '禁用' }}
        </button>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无用户</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()

const SEX_MAP = { 0: '女', 1: '男', 2: '保密' }

const searchForm = reactive({ nickNameFuzzy: '', status: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

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
    const params = { pageNo: next, pageSize: 12 }
    if (searchForm.nickNameFuzzy) params.nickNameFuzzy = searchForm.nickNameFuzzy
    if (searchForm.status !== '') params.status = searchForm.status
    const result = await proxy.Request({ url: proxy.Api.loadUser, params, showLoading: false })
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

const changeStatus = (u) => {
  proxy.Confirm({
    message: `确定要${u.status == 0 ? '启用' : '禁用'}【${u.nickName}】吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.changeStatus,
        params: { userId: u.userId, status: u.status == 0 ? 1 : 0 }
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
.m-user {
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

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m-user-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;

  .user-info {
    flex: 1;
    min-width: 0;
  }

  .user-top {
    display: flex;
    align-items: center;
    gap: 8px;

    .user-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--m-ink);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .user-sex {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .user-mail {
    margin-top: 3px;
    font-size: 12px;
    color: var(--m-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .user-id {
    margin-top: 2px;
    font-size: 11px;
    color: var(--m-ink-3);
    word-break: break-all;
  }

  .user-time {
    margin-top: 2px;
    font-size: 11px;
    color: var(--m-ink-3);
  }

  .user-op {
    flex-shrink: 0;
    height: 32px;
    padding: 0 14px;
    border: 1px solid rgba(120, 120, 128, 0.24);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.5);
    color: var(--m-ink-2);
    font-size: 12px;
    cursor: pointer;
    transition: transform 0.15s;

    &:active {
      transform: scale(0.95);
    }

    &.danger {
      color: var(--m-danger);
      border-color: rgba(255, 59, 48, 0.3);
    }
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

  &.danger {
    background: rgba(255, 59, 48, 0.14);
    color: var(--m-danger);
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
