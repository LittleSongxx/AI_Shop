<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.addresseeFuzzy" class="search-input" placeholder="收货人" @keyup.enter="reload" />
      <input v-model="searchForm.phoneFuzzy" class="search-input" placeholder="手机号" @keyup.enter="reload" />
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.addressId" class="glass-card addr-card">
        <div class="addr-head">
          <span class="addr-name">{{ row.addressee }}</span>
          <span class="addr-phone">{{ row.phone }}</span>
          <span v-if="row.defaultType == 1" class="m-tag green">默认</span>
          <button type="button" class="addr-del" @click="delRow(row)">删除</button>
        </div>
        <p class="addr-text">{{ row.address }}</p>
        <p class="addr-uid">用户ID：{{ row.userId }}</p>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无收货地址</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ addresseeFuzzy: '', phoneFuzzy: '' })
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
    if (searchForm.addresseeFuzzy) params.addresseeFuzzy = searchForm.addresseeFuzzy
    if (searchForm.phoneFuzzy) params.phoneFuzzy = searchForm.phoneFuzzy
    const result = await proxy.Request({ url: proxy.Api.userAddressLoadList, params, showLoading: false })
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
    message: `确定删除用户 ${row.userId} 的地址吗？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.userAddressDelete, params: { addressId: row.addressId } })
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
  border-radius: 16px;

  .search-input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    font-size: 14px;
    color: var(--m-ink);
    outline: none;

    & + .search-input {
      border-left: 1px solid rgba(120, 120, 128, 0.2);
      padding-left: 8px;
    }

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

.addr-card {
  padding: 12px 14px;

  .addr-head {
    display: flex;
    align-items: center;
    gap: 8px;

    .addr-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--m-ink);
    }

    .addr-phone {
      font-size: 13px;
      color: var(--m-ink-2);
    }

    .addr-del {
      margin-left: auto;
      border: none;
      background: transparent;
      color: var(--m-danger);
      font-size: 12px;
      cursor: pointer;
    }
  }

  .addr-text {
    margin: 8px 0 4px;
    font-size: 13px;
    color: var(--m-ink);
    line-height: 1.45;
  }

  .addr-uid {
    margin: 0;
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
