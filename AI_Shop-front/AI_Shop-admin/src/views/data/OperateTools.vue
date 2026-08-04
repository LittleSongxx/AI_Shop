<template>
  <div class="operate-tools-page">
    <el-card class="tool-card">
      <template #header>
        <span>数据同步</span>
      </template>
      <div class="tool-grid">
        <el-button type="primary" @click="runTool('toolStatistics')">同步统计数据</el-button>
        <el-button type="primary" @click="runTool('toolProductData')">同步商品向量索引</el-button>
        <el-button type="primary" @click="runTool('toolRagData')">同步 RAG 知识库</el-button>
      </div>
      <p class="tool-hint">将触发后台统计任务或 MQ 同步，数据量大时请耐心等待。</p>
    </el-card>

    <el-card class="tool-card">
      <template #header>
        <span>订单与秒杀</span>
      </template>
      <div class="tool-grid">
        <el-button type="warning" @click="runTool('toolAddAllOrderToDelayQueue')">
          待付款订单加入延时队列
        </el-button>
        <el-button type="danger" plain @click="runApi('warmupRushStock', {}, '预热全部秒杀券 Redis 库存？')">
          秒杀券 Redis 全量预热
        </el-button>
        <el-button type="danger" plain @click="runApi('reconcileRushStock', {}, '对全部秒杀券执行 Redis/DB 对账？')">
          秒杀券 Redis 全量对账
        </el-button>
      </div>
      <p class="tool-hint">延时队列用于支付超时关单；秒杀操作请先在「营销 → 优惠券管理」配置秒杀券。</p>
    </el-card>
    <el-card class="tool-card">
      <template #header>
        <span>签到数据</span>
      </template>
      <div class="tool-grid">
        <el-button type="warning" @click="runApi('signRecordSyncAllFromDb', { force: true }, '将 DB 全部签到汇总覆盖同步到 Redis？')">
          同步签到汇总（Hash）
        </el-button>
      </div>
      <p class="tool-hint">
        仅恢复连续天数、总天数、补签次数（Redis Hash），不含签到日历 Bitmap。
        历史签到日期请前往「营销 → 签到发券配置 → 同步历史签到日期」，默认截止昨天、不含今天。
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()

const runTool = (apiKey) => {
  proxy.Confirm({
    message: '确定执行该操作吗？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api[apiKey],
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('操作成功')
    },
  })
}

const runApi = (apiKey, params, confirmMsg) => {
  proxy.Confirm({
    message: confirmMsg,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api[apiKey],
        params,
        showLoading: true,
      })
      if (!result) return
      const d = result.data
      if (d && typeof d.synced === 'number') {
        proxy.Message.success(`同步 ${d.synced} 条，跳过 ${d.skipped ?? 0} 条（DB 共 ${d.totalInDb ?? 0} 条）`)
      } else {
        proxy.Message.success('操作成功')
      }
    },
  })
}
</script>

<style scoped lang="scss">
.operate-tools-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 960px;
}

.tool-card {
  border-radius: 8px;
}

.tool-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.tool-hint {
  margin: 12px 0 0;
  font-size: 13px;
  color: #888;
  line-height: 1.5;
}
</style>
