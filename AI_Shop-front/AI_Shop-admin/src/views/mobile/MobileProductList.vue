<template>
  <div class="m-product">

    <div class="m-search glass-card glass-strong">
      <span class="iconfont icon-search search-icon"></span>
      <input
        v-model="searchForm.productNameFuzzy"
        class="search-input"
        type="search"
        placeholder="搜索商品名称"
        @keyup.enter="reload"
      />
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option value="">全部</option>
        <option :value="1">已上架</option>
        <option :value="0">未上架</option>
      </select>
    </div>

    <button type="button" class="m-add-btn" @click="goEdit()">
      <span class="iconfont icon-add"></span> 发布商品
    </button>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.productId" class="m-prod-card glass-card">
        <div class="prod-top">
          <Cover :source="firstImg(row.cover)" :width="76" border-radius="12px" class="prod-cover"></Cover>
          <div class="prod-main">
            <div class="prod-name">{{ row.productName }}</div>
            <div class="prod-meta">
              <span class="prod-price">¥{{ priceText(row) }}</span>
              <span class="prod-stock">库存 {{ row.totalStock }}</span>
            </div>
            <div class="prod-tags">
              <span class="m-tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
              <span v-if="row.commendType == 1" class="m-tag gold">已推荐</span>
            </div>
          </div>
        </div>
        <div v-if="row.status != -1" class="prod-actions">
          <button type="button" class="act-btn" @click="goEdit(row.productId)">编辑</button>
          <button type="button" class="act-btn" @click="updateStock(row)">库存</button>
          <button
            type="button"
            class="act-btn"
            :class="{ 'is-disabled': row.commendType == 0 && !canCommend(row) }"
            :title="row.commendType == 0 ? getCommendBlockReason(row) : ''"
            @click="commend(row)"
          >
            {{ row.commendType == 1 ? '取消推荐' : '推荐' }}
          </button>
          <button
            type="button"
            class="act-btn"
            :class="{ 'is-disabled': row.status == 1 && !canDelist(row) }"
            :title="row.status == 1 ? getDelistBlockReason(row) : ''"
            @click="changeStatus(row)"
          >
            {{ row.status == 0 ? '上架' : '下架' }}
          </button>
          <button
            type="button"
            class="act-btn danger"
            :class="{ 'is-disabled': !canDelete(row) }"
            :title="getDeleteBlockReason(row)"
            @click="del(row)"
          >
            删除
          </button>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无商品</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <ProductStock ref="productStockRef"></ProductStock>
  </div>
</template>

<script setup>
import ProductStock from '@/views/product/edit/ProductStock.vue'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { mitter } from '@/eventbus/eventBus.js'
import {
  canCommend,
  canDelist,
  canDelete,
  getCommendBlockReason,
  getDelistBlockReason,
  getDeleteBlockReason
} from '@/utils/productRules.js'

const { proxy } = getCurrentInstance()
const router = useRouter()

const searchForm = reactive({ productNameFuzzy: '', status: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

const firstImg = (cover) => (cover ? String(cover).split(',')[0] : '')
const priceText = (row) => {
  const min = Number(row.minPrice || 0).toFixed(2)
  const max = Number(row.maxPrice || 0).toFixed(2)
  return min === max ? min : `${min}~${max}`
}
const statusText = (s) => (s == 1 ? '已上架' : s == -1 ? '已删除' : '未上架')
const statusClass = (s) => (s == 1 ? 'green' : s == -1 ? 'danger' : 'muted')
const goEdit = (id) => router.push(id ? `/m/product/edit/${id}` : '/m/product/edit')

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
    if (searchForm.productNameFuzzy) params.productNameFuzzy = searchForm.productNameFuzzy
    if (searchForm.status !== '') params.status = searchForm.status
    const result = await proxy.Request({ url: proxy.Api.loadProduct, params, showLoading: false })
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

const refreshRow = ({ productId, totalStock }) => {
  const row = list.value.find((it) => it.productId == productId)
  if (row && totalStock != null) row.totalStock = totalStock
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
  mitter.on('updateStockCallback', refreshRow)
})

onUnmounted(() => {
  observer && observer.disconnect()
  observer = null
  mitter.off('updateStockCallback')
})

const productStockRef = ref()
const updateStock = (row) => productStockRef.value.show(row.productId)

const changeStatus = (row) => {
  if (row.status == 1) {
    const block = getDelistBlockReason(row)
    if (block) {
      proxy.Message.warning(block)
      return
    }
  }
  proxy.ConfirmSensitive({
    message: `确定要【${row.status == 0 ? '上架' : '下架'}】该商品吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      const result = await proxy.Request({
        url: proxy.Api.updateProductStatus,
        sensitiveConfirmPwd,
        params: { productId: row.productId, status: row.status == 0 ? 1 : 0 }
      })
      if (!result) return
      proxy.Message.success('操作成功')
      reload()
    }
  })
}

const commend = (row) => {
  if (row.commendType == 0) {
    const block = getCommendBlockReason(row)
    if (block) {
      proxy.Message.warning(block)
      return
    }
  }
  proxy.Confirm({
    message: `确定要【${row.commendType == 0 ? '推荐' : '取消推荐'}】吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.commendProduct,
        params: { productId: row.productId, commendType: row.commendType == 0 ? 1 : 0 }
      })
      if (!result) return
      proxy.Message.success('操作成功')
      reload()
    }
  })
}

const del = (row) => {
  const block = getDeleteBlockReason(row)
  if (block) {
    proxy.Message.warning(block)
    return
  }
  proxy.ConfirmSensitive({
    message: `确定要删除【${row.productName}】吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      const result = await proxy.Request({
        url: proxy.Api.deleteProduct,
        sensitiveConfirmPwd,
        params: { productId: row.productId }
      })
      if (!result) return
      proxy.Message.success('操作成功')
      reload()
    }
  })
}
</script>

<style lang="scss" scoped>
.m-product {
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
    border-radius: 10px;
    padding: 4px 6px;
    font-size: 13px;
    color: var(--m-ink-2);
    outline: none;
  }
}

.m-add-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 42px;
  border: none;
  border-radius: 14px;
  background: var(--m-ink);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.15s, opacity 0.2s;

  &:active {
    transform: scale(0.98);
    opacity: 0.9;
  }
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m-prod-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;

  .prod-top {
    display: flex;
    gap: 12px;
  }

  .prod-cover {
    flex-shrink: 0;
  }

  .prod-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .prod-name {
    font-size: 14px;
    font-weight: 500;
    color: var(--m-ink);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .prod-meta {
    display: flex;
    align-items: baseline;
    gap: 12px;

    .prod-price {
      font-size: 16px;
      font-weight: 700;
      color: var(--m-ink);
    }

    .prod-stock {
      font-size: 12px;
      color: var(--m-ink-3);
    }
  }
}

.prod-tags {
  display: flex;
  gap: 6px;
}

.prod-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid rgba(120, 120, 128, 0.16);

  .act-btn {
    flex: 1;
    min-width: 56px;
    height: 32px;
    border: 1px solid rgba(120, 120, 128, 0.24);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.5);
    color: var(--m-ink-2);
    font-size: 12px;
    cursor: pointer;
    transition: transform 0.15s, background 0.2s, color 0.2s;

    &:active {
      transform: scale(0.95);
      background: var(--m-gold-soft);
      color: var(--m-ink);
    }

    &.danger {
      color: var(--m-danger);
      border-color: rgba(255, 59, 48, 0.3);
    }

    &.is-disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
  }
}

.m-tag {
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 500;

  &.green {
    background: rgba(52, 199, 89, 0.16);
    color: #1c8c3c;
  }

  &.muted {
    background: rgba(120, 120, 128, 0.16);
    color: var(--m-ink-2);
  }

  &.danger {
    background: rgba(255, 59, 48, 0.14);
    color: var(--m-danger);
  }

  &.gold {
    background: var(--m-gold-soft);
    color: #927535;
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
