<template>
  <div class="m-order">

    <div class="m-status-tabs">
      <button
        type="button"
        class="status-chip"
        :class="{ active: searchForm.orderStatus === '' }"
        @click="pickStatus('')"
      >
        全部
      </button>
      <button
        v-for="st in orderStatusList"
        :key="st.status"
        type="button"
        class="status-chip"
        :class="{ active: searchForm.orderStatus === st.status }"
        @click="pickStatus(st.status)"
      >
        {{ st.desc }}
      </button>
    </div>

    <div class="m-search glass-card glass-strong">
      <span class="iconfont icon-search search-icon"></span>
      <input
        v-model="searchForm.productNameFuzzy"
        class="search-input"
        type="search"
        placeholder="搜索商品名称"
        @keyup.enter="reload"
      />
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="order in list" :key="order.orderId" class="m-order-card glass-card">
        <div class="order-head">
          <span class="order-no">#{{ order.orderId }}</span>
          <span class="order-status">{{ order.orderStatusName }}</span>
        </div>
        <div class="order-meta">
          <span>{{ order.orderTime }}</span>
          <span>买家：{{ order.nickName }} (ID:{{ order.userId }})</span>
        </div>
        <div class="order-goods">
          <div v-for="(sub, i) in order.orderItemList" :key="i" class="goods-row">
            <CouponOrderCover v-if="isCouponItem(order, sub)" :width="48" border-radius="10px" />
            <Cover v-else :source="sub.cover" :width="48" border-radius="10px"></Cover>
            <div class="goods-info">
              <span class="goods-name">{{ sub.productName }}</span>
              <span class="goods-prop">{{ sub.propertyInfo }}</span>
              <span class="goods-remark">买家备注：{{ sub.remark?.trim() || '暂无' }}</span>
            </div>
            <div class="goods-amount">
              <span class="amt">¥{{ amount(sub.itemAmount) }}</span>
              <span class="cnt">x{{ sub.buyCount }}</span>
            </div>
          </div>
        </div>
        <div class="order-foot">
          <div class="pay-summary">
            <p v-if="hasCoupon(order)" class="origin-line">商品总价 ¥{{ amount(order.originalAmount) }}</p>
            <p v-if="hasCoupon(order)" class="coupon-line">
              优惠券：{{ couponText(order) }} · -¥{{ amount(order.couponDiscountAmount) }}
            </p>
            <p class="pay-amount">实付 <strong>¥{{ amount(order.amount) }}</strong></p>
          </div>
          <div class="order-ops">
            <button v-if="order.orderStatus == 1" type="button" class="op-btn primary" @click="deliver(order)">
              发货
            </button>
            <button v-if="order.logisticsNo" type="button" class="op-btn" @click="viewLogistics(order)">
              物流
            </button>
            <button v-if="order.commentStatus != 0" type="button" class="op-btn" @click="reply(order)">
              回复
            </button>
          </div>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无订单</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <Delivery ref="deliveryRef" @reload="reload"></Delivery>
    <CommentReply ref="commentRef" @reload="reload"></CommentReply>

    <Dialog :show="logisticsShow" title="物流信息" width="92%" :showCancel="false" :buttons="[]" @close="logisticsShow = false">
      <div v-if="logisticsInfo" class="logistics-box">
        <p><b>{{ logisticsInfo.logisticsCompany }}</b> · {{ logisticsInfo.logisticsNo }}</p>
        <div v-if="logisticsTraces.length" class="trace-list">
          <div v-for="(t, i) in logisticsTraces" :key="i" class="trace-item">
            <span class="trace-time">{{ t.time || t.AcceptTime }}</span>
            <span class="trace-text">{{ t.context || t.AcceptStation }}</span>
          </div>
        </div>
        <p v-else class="m-empty-tip">暂无物流轨迹</p>
      </div>
    </Dialog>
  </div>
</template>

<script setup>
import Delivery from '@/views/order/Delivery.vue'
import CommentReply from '@/views/order/CommentReply.vue'
import { isCouponOrder, isCouponOrderItem } from '@/utils/order.js'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'

const { proxy } = getCurrentInstance()
const route = useRoute()

const orderStatusList = ref([])
const searchForm = reactive({ orderStatus: '', productNameFuzzy: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
let observer = null

const amount = (v) => Number(v || 0).toFixed(2)

const isCouponItem = (order, item) => isCouponOrder(order) || isCouponOrderItem(item)

const hasCoupon = (order) => Number(order?.couponDiscountAmount ?? 0) > 0

const couponText = (order) => {
  const name = order?.couponName
  if (!name) return '优惠券'
  const typeMap = { 1: '满减券', 2: '折扣券', 3: '无门槛券' }
  const type = typeMap[order?.couponType]
  return type ? `${name}（${type}）` : String(name)
}

const loadOrderStatus = async () => {
  const result = await proxy.Request({ url: proxy.Api.loadOrderStatus, showLoading: false })
  if (result) orderStatusList.value = result.data || []
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
    if (searchForm.orderStatus !== '') params.orderStatus = searchForm.orderStatus
    if (searchForm.productNameFuzzy) params.productNameFuzzy = searchForm.productNameFuzzy
    const result = await proxy.Request({ url: proxy.Api.loadOrder, params, showLoading: false })
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
const pickStatus = (status) => {
  searchForm.orderStatus = status
  reload()
}

const deliveryRef = ref()
const deliver = (order) => deliveryRef.value.show(order.orderId)

const commentRef = ref()
const reply = (order) => commentRef.value.show(order.orderId)

const logisticsShow = ref(false)
const logisticsInfo = ref(null)
const logisticsTraces = ref([])
const viewLogistics = async (order) => {
  const result = await proxy.Request({
    url: proxy.Api.getLogistics,
    params: { orderId: order.orderId }
  })
  if (!result) return
  logisticsInfo.value = result.data || {}
  const traces = result.data && (result.data.traces || result.data.list || result.data.Traces)
  logisticsTraces.value = Array.isArray(traces) ? traces : []
  logisticsShow.value = true
}

onMounted(() => {
  loadOrderStatus()
  if (route.query.status != null && route.query.status !== '') {
    searchForm.orderStatus = Number(route.query.status)
  }
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
.m-order {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-status-tabs {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }

  .status-chip {
    flex-shrink: 0;
    padding: 7px 14px;
    border: none;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.55);
    color: var(--m-ink-2);
    font-size: 13px;
    cursor: pointer;
    transition: background 0.2s, color 0.2s;

    &.active {
      background: var(--m-ink);
      color: #fff;
    }
  }
}

.m-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 42px;
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

.m-order-card {
  padding: 12px 14px;

  .order-head {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .order-no {
      font-size: 13px;
      color: var(--m-ink-2);
    }

    .order-status {
      font-size: 13px;
      font-weight: 600;
      color: var(--m-gold);
    }
  }

  .order-meta {
    display: flex;
    gap: 14px;
    margin-top: 4px;
    font-size: 11px;
    color: var(--m-ink-3);
  }

  .order-goods {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .goods-row {
    display: flex;
    align-items: center;
    gap: 10px;

    .goods-info {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
    }

    .goods-name {
      font-size: 13px;
      color: var(--m-ink);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .goods-prop {
      font-size: 11px;
      color: var(--m-ink-3);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .goods-remark {
      margin-top: 4px;
      font-size: 11px;
      line-height: 1.4;
      color: #000;
      word-break: break-all;
    }

    .goods-amount {
      flex-shrink: 0;
      text-align: right;

      .amt {
        display: block;
        font-size: 13px;
        font-weight: 600;
        color: var(--m-ink);
      }

      .cnt {
        font-size: 11px;
        color: var(--m-ink-3);
      }
    }
  }

  .order-foot {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid rgba(120, 120, 128, 0.16);

    .pay-summary {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .origin-line,
    .coupon-line {
      margin: 0;
      font-size: 11px;
      color: var(--m-ink-3);
      word-break: break-all;
    }

    .coupon-line {
      color: #c45c26;
    }

    .pay-amount {
      margin: 2px 0 0;
      font-size: 12px;
      color: var(--m-ink-2);

      strong {
        font-size: 16px;
        color: var(--m-ink);
      }
    }
  }

  .order-ops {
    display: flex;
    gap: 8px;

    .op-btn {
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

      &.primary {
        background: var(--m-ink);
        border-color: var(--m-ink);
        color: #fff;
      }
    }
  }
}

.logistics-box {
  font-size: 13px;
  color: var(--m-ink);

  .trace-list {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .trace-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-left: 12px;
    border-left: 2px solid var(--m-gold-soft);

    .trace-time {
      font-size: 11px;
      color: var(--m-ink-3);
    }

    .trace-text {
      font-size: 13px;
      color: var(--m-ink-2);
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
