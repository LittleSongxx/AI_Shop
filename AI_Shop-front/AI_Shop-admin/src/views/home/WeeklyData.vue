<template>
  <section class="trend-section">
    <header class="section-head">
      <h3 class="section-title">经营趋势</h3>
      <div class="chart-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          :class="['chart-tab', { 'is-active': activeTab === tab.key }]"
          @click="switchTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </header>

    <div class="chart-main card">
      <div class="stat-row">
        <div class="stat-chip" v-for="item in asideStats" :key="item.key">
          <span class="stat-chip__label">{{ item.label }}</span>
          <span class="stat-chip__value">{{ item.value }}</span>
          <span class="stat-chip__sub">{{ item.sub }}</span>
        </div>
      </div>
      <div class="chart-canvas" ref="chartRef"></div>
    </div>
  </section>
</template>

<script setup>
import { ref, getCurrentInstance, nextTick, shallowRef, onMounted, onUnmounted, computed } from 'vue'
import * as echarts from 'echarts'

const { proxy } = getCurrentInstance()

const tabs = [
  { key: 'sale', label: '销售' },
  { key: 'refund', label: '退款' },
]

const activeTab = ref('sale')
const chartRef = ref(null)
const chartInstance = shallowRef()

const saleData = ref({ date: [], orderAmount: [], orderCount: [] })
const refundData = ref({ date: [], orderAmount: [], orderCount: [] })

const sum = (arr) => (arr || []).reduce((a, b) => a + Number(b || 0), 0)
const fmt = (n, amount) => proxy.Utils.formatNumber(n, amount)

const asideStats = computed(() => {
  const isSale = activeTab.value === 'sale'
  const data = isSale ? saleData.value : refundData.value
  const amountTotal = sum(data.orderAmount)
  const countTotal = sum(data.orderCount)
  const prefix = isSale ? '销售' : '退款'
  return [
    {
      key: 'amount',
      label: `7日${prefix}额`,
      value: fmt(amountTotal, true),
      sub: `日均 ${fmt(Math.round(amountTotal / 7), true)}`,
    },
    {
      key: 'count',
      label: `7日${prefix}单`,
      value: fmt(countTotal, false),
      sub: `日均 ${fmt(Math.round(countTotal / 7), false)}`,
    },
  ]
})

const getChartOption = () => {
  const isSale = activeTab.value === 'sale'
  const data = isSale ? saleData.value : refundData.value
  const accent = isSale ? '#c9754f' : '#ff6b5a'
  const accentSoft = isSale ? 'rgba(201, 117, 79, 0.14)' : 'rgba(255, 107, 90, 0.12)'

  return {
    animationDuration: 600,
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(42, 36, 32, 0.92)',
      borderWidth: 0,
      textStyle: { color: '#fff', fontSize: 12 },
      formatter(params) {
        const date = params[0]?.axisValue || ''
        const amount = params.find((p) => p.seriesName === '金额')?.value ?? 0
        const count = params.find((p) => p.seriesName === '数量')?.value ?? 0
        return `${date}<br/>金额：${amount} 元<br/>数量：${count} 单`
      },
    },
    legend: {
      data: ['金额', '数量'],
      right: 0,
      top: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: '#8a8278', fontSize: 11 },
    },
    grid: { left: 4, right: 4, bottom: 0, top: 28, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: data.date,
      axisLine: { lineStyle: { color: '#e8e2da' } },
      axisTick: { show: false },
      axisLabel: { color: '#a39a90', fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value',
        splitLine: { lineStyle: { type: 'dashed', color: '#f0ebe4' } },
        axisLabel: { color: '#a39a90', fontSize: 11 },
      },
      {
        type: 'value',
        splitLine: { show: false },
        axisLabel: { color: '#a39a90', fontSize: 11 },
      },
    ],
    series: [
      {
        name: '金额',
        type: 'line',
        smooth: 0.35,
        data: data.orderAmount,
        yAxisIndex: 0,
        lineStyle: { color: accent, width: 2.5 },
        itemStyle: { color: accent },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: accentSoft },
            { offset: 1, color: 'rgba(255,255,255,0)' },
          ]),
        },
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: false,
        emphasis: { focus: 'series', showSymbol: true },
      },
      {
        name: '数量',
        type: 'bar',
        data: data.orderCount,
        yAxisIndex: 1,
        barMaxWidth: 14,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(42, 36, 32, 0.55)' },
            { offset: 1, color: 'rgba(42, 36, 32, 0.12)' },
          ]),
          borderRadius: [4, 4, 0, 0],
        },
      },
    ],
  }
}

const renderChart = () => {
  if (!chartInstance.value) return
  chartInstance.value.setOption(getChartOption(), true)
}

const switchTab = (key) => {
  activeTab.value = key
  renderChart()
}

const loadStatisticsData = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadWeeklyStatisticsData,
    params: {},
  })
  if (!result) return

  const saleAmountData = result.data.find((item) => item.dataType == 1)
  const saleCountData = result.data.find((item) => item.dataType == 2)
  const refundAmountData = result.data.find((item) => item.dataType == 3)
  const refundCountData = result.data.find((item) => item.dataType == 4)

  saleData.value = {
    date: saleAmountData.dateList,
    orderAmount: saleAmountData.dataList,
    orderCount: saleCountData.dataList,
  }
  refundData.value = {
    date: refundAmountData.dateList,
    orderAmount: refundAmountData.dataList,
    orderCount: refundCountData.dataList,
  }
  renderChart()
}

const onResize = () => chartInstance.value?.resize()

const init = async () => {
  await nextTick()
  chartInstance.value = echarts.init(chartRef.value)
  loadStatisticsData()
  window.addEventListener('resize', onResize)
}

onMounted(() => {
  init()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  chartInstance.value?.dispose()
})
</script>

<style lang="scss" scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--header-border);
  box-shadow: var(--shadow-card);
  border-radius: var(--card-radius);
}

.trend-section {
  margin-top: 10px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;

  .section-title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
  }
}

.chart-tabs {
  display: inline-flex;
  padding: 3px;
  border-radius: 999px;
  background: var(--primary-muted);
  border: 1px solid var(--header-border);
}

.chart-tab {
  border: none;
  background: transparent;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--text3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;

  &.is-active {
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow-sm);
    font-weight: 600;
  }
}

.chart-main {
  padding: 10px 12px 6px;
}

.stat-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.stat-chip {
  flex: 1;
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--primary-muted);

  &__label {
    font-size: 11px;
    color: var(--text3);
    white-space: nowrap;
  }

  &__value {
    font-size: 13px;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
  }

  &__sub {
    margin-left: auto;
    font-size: 10px;
    color: var(--text3);
    white-space: nowrap;
  }
}

.chart-canvas {
  width: 100%;
  height: 158px;
}

@media (max-width: 720px) {
  .stat-row {
    flex-direction: column;
  }

  .stat-chip__sub {
    margin-left: 0;
  }
}
</style>
