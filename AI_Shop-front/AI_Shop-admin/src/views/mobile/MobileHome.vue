<template>
  <div class="m-home">

    <section class="m-stat-grid">
      <div v-for="item in todayFields" :key="item.key" class="m-stat glass-card">
        <div class="stat-icon iconfont" :class="`icon-${item.icon}`"></div>
        <div class="stat-body">
          <span class="stat-name">{{ item.name }}</span>
          <span class="stat-value">{{ proxy.Utils.formatNumber(item.value, item.amount) }}</span>
          <span class="stat-yesterday">
            昨日 {{ proxy.Utils.formatNumber(item.yesterday, item.amount) }}
            <em :class="riseClass(item.increase)">{{ formatPercent(item.increase) }}</em>
          </span>
        </div>
      </div>
    </section>

    <section class="glass-card m-quick">
      <h3 class="m-block-title">快捷操作</h3>
      <div class="quick-grid">
        <button v-for="q in quickEntries" :key="q.path" type="button" class="quick-item" @click="go(q.path)">
          <span class="iconfont quick-icon" :class="`icon-${q.icon}`"></span>
          <span class="quick-label">{{ q.label }}</span>
        </button>
      </div>
    </section>

    <section class="glass-card m-weekly">
      <h3 class="m-block-title">近 7 日销售</h3>
      <div v-if="weekly.length" class="weekly-chart">
        <div v-for="(d, i) in weekly" :key="i" class="weekly-col">
          <div class="bar-track">
            <div class="bar-fill" :style="{ height: barHeight(d.amount) }"></div>
          </div>
          <span class="bar-amount">{{ shortAmount(d.amount) }}</span>
          <span class="bar-day">{{ d.label }}</span>
        </div>
      </div>
      <p v-else class="m-empty-tip">暂无统计数据</p>
    </section>

    <section class="glass-card m-stock">
      <div class="m-block-head">
        <h3 class="m-block-title">库存预警</h3>
        <button type="button" class="link-more" @click="go('/m/product')">去管理</button>
      </div>
      <div v-if="lessStock.length" class="stock-list">
        <div v-for="row in lessStock" :key="row.productId + (row.propertyValueIdHash || '')" class="stock-row">
          <Cover :source="firstImg(row.productCover)" :width="48" border-radius="10px"></Cover>
          <div class="stock-info">
            <span class="stock-name">{{ row.productName }}</span>
            <span class="stock-prop">{{ propText(row.propertyData) }}</span>
          </div>
          <span class="stock-num" :class="{ danger: row.stock <= 5 }">剩 {{ row.stock }}</span>
        </div>
      </div>
      <p v-else class="m-empty-tip">暂无库存预警</p>
    </section>
  </div>
</template>

<script setup>
import { ref, getCurrentInstance, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const { proxy } = getCurrentInstance()
const router = useRouter()

const todayFields = ref([
  { key: 'orderAmount', name: '今日销售额', icon: 'sale-amount', amount: true, value: 0, yesterday: 0, increase: 0 },
  { key: 'orderCount', name: '今日订单', icon: 'order-count', amount: false, value: 0, yesterday: 0, increase: 0 },
  { key: 'userCount', name: '新增用户', icon: 'user-add', amount: false, value: 0, yesterday: 0, increase: 0 },
  { key: 'refundAmount', name: '今日退款', icon: 'refund-amount', amount: true, value: 0, yesterday: 0, increase: 0 }
])

const weekly = ref([])
const lessStock = ref([])
const maxAmount = ref(1)

const quickEntries = [
  { label: '发布商品', path: '/m/product/edit', icon: 'add' },
  { label: '待发货', path: '/m/order?status=1', icon: 'order' },
  { label: '评价回复', path: '/m/order/comment', icon: 'commend' },
  { label: 'MQ补偿日志', path: '/m/more/mqLog', icon: 'setting' },
  { label: '客服记录', path: '/m/more/agent', icon: 'robot' },
]

const riseClass = (v) => (v > 0 ? 'rise' : v < 0 ? 'decline' : 'flat')
const formatPercent = (v) => `${v > 0 ? '+' : ''}${(Number(v) || 0).toFixed(1)}%`
const firstImg = (cover) => (cover ? String(cover).split(',')[0] : '')
const propText = (arr) =>
  Array.isArray(arr) ? arr.map((p) => `${p.propertyName}:${p.propertyValue}`).join(' / ') : ''

const shortAmount = (v) => {
  const n = Number(v) || 0
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(Math.round(n))
}
const barHeight = (v) => {
  const ratio = maxAmount.value > 0 ? (Number(v) || 0) / maxAmount.value : 0
  return Math.max(4, Math.round(ratio * 100)) + '%'
}

const getToday = async () => {
  const result = await proxy.Request({ url: proxy.Api.getTodayData, showLoading: false })
  if (!result) return
  const map = new Map(result.data.map((it) => [it.type, it]))
  todayFields.value = todayFields.value.map((it) => {
    const d = map.get(it.key) || {}
    return { ...it, value: d.todayValue || 0, yesterday: d.yesterdayValue || 0, increase: d.increase || 0 }
  })
}

const getWeekly = async () => {
  const result = await proxy.Request({ url: proxy.Api.loadWeeklyStatisticsData, showLoading: false })
  if (!result || !Array.isArray(result.data)) return
  const saleAmount = result.data.find(d => d.dataType === 1)
  if (!saleAmount) return
  const list = (saleAmount.dateList || []).map((date, i) => ({
    label: String(date).slice(5),
    amount: Number(saleAmount.dataList[i] || 0)
  }))
  weekly.value = list.slice(-7)
  maxAmount.value = Math.max(1, ...weekly.value.map((d) => d.amount))
}

const getLessStock = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadLessStockProduct,
    params: { pageNo: 1, pageSize: 6 },
    showLoading: false
  })
  if (!result) return
  lessStock.value = (result.data && result.data.list) || []
}

const go = (path) => router.push(path)

onMounted(() => {
  getToday()
  getWeekly()
  getLessStock()
})
</script>

<style lang="scss" scoped>
.m-home {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-block-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--m-ink);
}

.m-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;

  .link-more {
    border: none;
    background: transparent;
    color: var(--m-gold);
    font-size: 13px;
    cursor: pointer;
  }
}

.m-empty-tip {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--m-ink-3);
  text-align: center;
}

.m-stat-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.m-stat {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 12px;

  .stat-icon {
    width: 42px;
    height: 42px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 8px;
    font-size: 21px;
    background: var(--m-gold-soft);
    color: var(--m-ink);

    &.icon-order-count {
      background: rgba(0, 113, 227, 0.12);
      color: var(--m-blue);
    }

    &.icon-user-add {
      background: rgba(52, 199, 89, 0.14);
      color: var(--m-green);
    }

    &.icon-refund-amount {
      background: rgba(255, 59, 48, 0.12);
      color: var(--m-danger);
    }
  }

  .stat-body {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .stat-name {
    font-size: 12px;
    color: var(--m-ink-3);
  }

  .stat-value {
    font-size: 19px;
    font-weight: 700;
    color: var(--m-ink);
    line-height: 1.25;
  }

  .stat-yesterday {
    font-size: 11px;
    color: var(--m-ink-3);

    em {
      font-style: normal;
      margin-left: 4px;

      &.rise {
        color: var(--m-danger);
      }

      &.decline {
        color: var(--m-green);
      }
    }
  }
}

.m-quick {
  padding: 14px;
}

.quick-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 12px;
}

.quick-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 4px;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.45);
  cursor: pointer;
  transition: transform 0.15s, background 0.2s;

  &:active {
    transform: scale(0.94);
    background: var(--m-gold-soft);
  }

  .quick-icon {
    font-size: 22px;
    color: var(--m-ink);
  }

  .quick-label {
    font-size: 11px;
    color: var(--m-ink-2);
  }
}

.m-weekly {
  padding: 14px;
}

.weekly-chart {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 6px;
  height: 140px;
  margin-top: 14px;
}

.weekly-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  height: 100%;

  .bar-track {
    flex: 1;
    width: 60%;
    max-width: 22px;
    display: flex;
    align-items: flex-end;
  }

  .bar-fill {
    width: 100%;
    border-radius: 8px 8px 4px 4px;
    background: linear-gradient(180deg, var(--m-gold) 0%, #b8923f 100%);
    transition: height 0.4s ease;
  }

  .bar-amount {
    font-size: 10px;
    color: var(--m-ink-2);
  }

  .bar-day {
    font-size: 10px;
    color: var(--m-ink-3);
  }
}

.m-stock {
  padding: 14px;
}

.stock-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stock-row {
  display: flex;
  align-items: center;
  gap: 10px;

  .stock-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .stock-name {
    font-size: 13px;
    color: var(--m-ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .stock-prop {
    font-size: 11px;
    color: var(--m-ink-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .stock-num {
    flex-shrink: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--m-ink-2);

    &.danger {
      color: var(--m-danger);
    }
  }
}
</style>
