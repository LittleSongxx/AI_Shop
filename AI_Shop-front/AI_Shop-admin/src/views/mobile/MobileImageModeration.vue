<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.userIdFuzzy" class="search-input" placeholder="用户ID" @keyup.enter="reload" />
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option :value="0">待复核</option>
        <option :value="undefined">全部</option>
        <option :value="1">已通过</option>
        <option :value="2">确认违规</option>
        <option :value="3">误报驳回</option>
      </select>
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.recordId" class="glass-card mod-card">
        <div class="mod-head">
          <span class="mod-scene">{{ sceneLabel(row.scene) }}</span>
          <span class="mod-status" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span>
        </div>
        <img v-if="row.imagePath" class="mod-img" :src="imageUrl(row.imagePath)" alt="" />
        <p class="mod-user">用户：{{ row.userId }}</p>
        <p class="mod-conc">{{ row.conclusion || '—' }}</p>
        <p class="mod-time">{{ row.createTime }}</p>
        <div class="mod-ops">
          <button v-if="row.status === 0" type="button" class="op-btn primary" @click="openHandle(row)">复核</button>
          <button v-else type="button" class="op-btn" @click="openHandle(row)">用户解封</button>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无待复核图片</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <HandleImageModeration ref="handleRef" @reload="reload" />
  </div>
</template>

<script setup>
import HandleImageModeration from '@/views/setting/HandleImageModeration.vue'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ userIdFuzzy: '', status: 0 })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
const handleRef = ref(null)
let observer = null

const imageUrl = (path) => `${proxy.Api.sourcePath}${encodeURIComponent(path)}`
const sceneLabel = (s) => (s === 'avatar' ? '头像' : s === 'comment' ? '评论' : s || '—')
const statusLabel = (s) => {
  if (s === 1) return '已通过'
  if (s === 2) return '确认违规'
  if (s === 3) return '误报驳回'
  return '待复核'
}
const statusClass = (s) => {
  if (s === 1) return 'done'
  if (s === 2) return 'danger'
  if (s === 3) return 'reject'
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
    if (searchForm.userIdFuzzy) params.userIdFuzzy = searchForm.userIdFuzzy
    if (searchForm.status !== undefined && searchForm.status !== '') params.status = Number(searchForm.status)
    const result = await proxy.Request({
      url: proxy.Api.imageModerationLoadList,
      params,
      showLoading: false
    })
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
const openHandle = (row) => handleRef.value?.show(row)

onMounted(() => {
  reload()
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadList()
    },
    { rootMargin: '120px' }
  )
  if (sentinel.value) observer.observe(sentinel.value)
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
})
</script>

<style lang="scss" scoped>
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m-search {
  display: flex;
  gap: 8px;
  padding: 10px 12px;

  .search-input {
    flex: 1;
    border: none;
    background: transparent;
    font-size: 14px;
    outline: none;
  }

  .search-select {
    border: none;
    background: rgba(255, 255, 255, 0.5);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 13px;
  }
}

.mod-card {
  padding: 12px;

  .mod-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }

  .mod-scene {
    font-size: 13px;
    font-weight: 600;
  }

  .mod-status {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;

    &.pending {
      background: rgba(255, 149, 0, 0.15);
      color: #ff9500;
    }
    &.done {
      background: rgba(52, 199, 89, 0.15);
      color: #34c759;
    }
    &.danger {
      background: rgba(255, 59, 48, 0.15);
      color: #ff3b30;
    }
    &.reject {
      background: rgba(142, 142, 147, 0.15);
      color: #8e8e93;
    }
  }

  .mod-img {
    width: 100%;
    max-height: 200px;
    object-fit: contain;
    border-radius: 10px;
    background: #f5f5f7;
    margin-bottom: 8px;
  }

  .mod-user,
  .mod-conc,
  .mod-time {
    margin: 0 0 4px;
    font-size: 12px;
    color: var(--m-ink-2);
    word-break: break-all;
  }

  .mod-ops {
    margin-top: 10px;
    display: flex;
    gap: 8px;
  }

  .op-btn {
    flex: 1;
    height: 34px;
    border-radius: 10px;
    border: 1px solid rgba(120, 120, 128, 0.24);
    background: rgba(255, 255, 255, 0.5);
    font-size: 13px;

    &.primary {
      color: var(--m-gold);
      border-color: rgba(201, 169, 98, 0.4);
    }
  }
}

.m-sentinel {
  text-align: center;
  padding: 12px;
  font-size: 12px;
  color: var(--m-ink-3);
}

.m-empty-tip {
  text-align: center;
  padding: 40px 16px;
  color: var(--m-ink-3);
  font-size: 14px;
}
</style>
