<template>
  <section class="dashboard-overview">
    <header class="overview-head">
      <h2 class="overview-title">今日概览</h2>
      <span class="overview-date">{{ todayLabel }}</span>
    </header>

    <div class="metric-bento">
      <article
        v-for="item in todayDataField"
        :key="item.key"
        :class="['metric-card', `metric-card--${item.key}`]"
      >
        <div class="metric-card__top">
          <div class="metric-icon" :style="{ background: item.bg, color: item.color }">
            <span :class="['iconfont', `icon-${item.icon}`]"></span>
          </div>
          <span :class="['trend-pill', trendClass(item.dataValue.increase)]">
            <span :class="['iconfont', changeIcon(item.dataValue.increase)]"></span>
            {{ formatTrend(item.dataValue.increase) }}
          </span>
        </div>
        <p class="metric-label">{{ item.name }}</p>
        <p class="metric-value">
          {{ proxy.Utils.formatNumber(item.dataValue.todayValue, item.amount) }}
        </p>
        <p class="metric-yesterday">
          昨日 {{ proxy.Utils.formatNumber(item.dataValue.yesterdayValue, item.amount) }}
        </p>
      </article>
    </div>
  </section>
</template>

<script setup>
import { ref, getCurrentInstance, onMounted, computed } from 'vue'

const { proxy } = getCurrentInstance()

const todayLabel = computed(() => {
  const d = new Date()
  const week = ['日', '一', '二', '三', '四', '五', '六']
  return `${d.getMonth() + 1}月${d.getDate()}日 · 周${week[d.getDay()]}`
})

const changeIcon = (increase) => {
  if (increase > 0) return 'icon-rise'
  if (increase < 0) return 'icon-decline'
  return 'icon-horizontal'
}

const trendClass = (increase) => {
  if (increase > 0) return 'is-up'
  if (increase < 0) return 'is-down'
  return 'is-flat'
}

const formatTrend = (increase) => {
  const n = Number(increase)
  if (Number.isNaN(n)) return '—'
  if (n === 0) return '持平'
  const text = proxy.Utils.formatNumber(Math.abs(n), false)
  return n > 0 ? `+${text}%` : `-${text}%`
}

const todayDataField = ref([
  {
    name: '今日销售额',
    icon: 'sale-amount',
    color: '#fff',
    bg: 'linear-gradient(135deg, #0f766e 0%, #2563eb 100%)',
    key: 'orderAmount',
    amount: true,
    dataValue: {},
  },
  {
    name: '今日订单',
    icon: 'order-count',
    color: '#17202a',
    bg: 'rgba(23, 32, 42, 0.08)',
    key: 'orderCount',
    dataValue: {},
  },
  {
    name: '新增用户',
    icon: 'user-add',
    color: '#0071e3',
    bg: 'rgba(0, 113, 227, 0.12)',
    key: 'userCount',
    dataValue: {},
  },
  {
    name: '今日退款',
    icon: 'refund-amount',
    color: '#ff3b30',
    bg: 'rgba(255, 59, 48, 0.12)',
    key: 'refundAmount',
    amount: true,
    dataValue: {},
  },
])

const getTodayData = async () => {
  const result = await proxy.Request({
    url: proxy.Api.getTodayData,
  })
  if (!result) return
  const todayData = new Map(result.data.map((item) => [item.type, item]))
  todayDataField.value = todayDataField.value.map((item) => ({
    ...item,
    dataValue: todayData.get(item.key) || {},
  }))
}

onMounted(() => {
  getTodayData()
})
</script>

<style lang="scss" scoped>
.dashboard-overview {
  margin-bottom: 4px;
}

.overview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;

  .overview-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
  }

  .overview-date {
    padding: 4px 10px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--header-border);
    font-size: 11px;
    color: var(--text3);
  }
}

.metric-bento {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) repeat(3, minmax(0, 1fr));
  grid-template-areas: 'hero order user refund';
  gap: 10px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--surface);
  border: 1px solid var(--header-border);
  box-shadow: var(--shadow-card);
  transition: box-shadow 0.2s ease;

  &:hover {
    box-shadow: var(--shadow-float);
  }

  &--orderAmount {
    grid-area: hero;
    background: linear-gradient(145deg, #0f766e 0%, #0b665f 100%);
    border-color: rgba(255, 255, 255, 0.08);

    .metric-label,
    .metric-yesterday {
      color: rgba(255, 255, 255, 0.55);
    }

    .metric-value {
      font-size: 22px;
      color: #fff;
    }

    .trend-pill {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.12);
      color: rgba(255, 255, 255, 0.85);
    }
  }

  &--orderCount {
    grid-area: order;
  }

  &--userCount {
    grid-area: user;
  }

  &--refundAmount {
    grid-area: refund;
  }

  &__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 8px;
  }
}

.metric-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  font-size: 16px;
  flex-shrink: 0;
}

.trend-pill {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 20px;
  padding: 0 7px;
  border-radius: 999px;
  border: 1px solid var(--header-border);
  background: var(--primary-soft);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;

  .iconfont {
    font-size: 10px;
  }

  &.is-up {
    color: #c44;
    background: rgba(255, 59, 48, 0.08);
    border-color: rgba(255, 59, 48, 0.15);
  }

  &.is-down {
    color: #1c8c3c;
    background: rgba(52, 199, 89, 0.1);
    border-color: rgba(52, 199, 89, 0.2);
  }

  &.is-flat {
    color: var(--text3);
  }
}

.metric-label {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--text3);
}

.metric-value {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--text);
  letter-spacing: 0;
}

.metric-yesterday {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--text3);
}

@media (max-width: 1100px) {
  .metric-bento {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-areas:
      'hero hero'
      'order user'
      'refund refund';
  }
}

@media (max-width: 640px) {
  .metric-bento {
    grid-template-columns: 1fr;
    grid-template-areas:
      'hero'
      'order'
      'user'
      'refund';
  }
}
</style>
