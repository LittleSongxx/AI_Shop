<template>
  <div class="m-simple">
    <section class="glass-card m-tool-group">
      <h3 class="m-group-title">数据同步</h3>
      <button type="button" class="tool-btn" @click="runTool('toolStatistics', '同步统计数据')">同步统计数据</button>
      <button type="button" class="tool-btn" @click="runTool('toolProductData', '同步商品向量索引')">同步商品向量索引</button>
      <button type="button" class="tool-btn" @click="runTool('toolRagData', '同步 RAG 知识库')">同步 RAG 知识库</button>
    </section>

    <section class="glass-card m-tool-group">
      <h3 class="m-group-title">订单与秒杀</h3>
      <button type="button" class="tool-btn warn" @click="runTool('toolAddAllOrderToDelayQueue', '待付款订单加入延时队列')">
        待付款订单加入延时队列
      </button>
      <button type="button" class="tool-btn danger" @click="runTool('warmupRushStock', '预热全部秒杀券 Redis 库存')">
        秒杀券 Redis 全量预热
      </button>
      <button type="button" class="tool-btn danger" @click="runTool('reconcileRushStock', '对全部秒杀券执行 Redis/DB 对账')">
        秒杀券 Redis 全量对账
      </button>
      <p class="tool-hint">延时队列用于支付超时关单；秒杀操作请先在优惠券中配置秒杀券。</p>
    </section>
    <section class="glass-card m-tool-group">
      <h3 class="m-group-title">签到数据</h3>
      <button type="button" class="tool-btn warn" @click="runTool('signRecordSyncAllFromDb', '签到 DB→Redis 全量同步', { force: true })">
        签到数据 DB → Redis 同步
      </button>
      <p class="tool-hint">
        仅恢复连续/总天数与补签次数（Hash），不含签到日历。
        历史签到日期请在「签到发券」页操作，默认截止昨天、不含今天。
      </p>
    </section>
  </div>
</template>

<script setup>
import { getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()

const runTool = (apiKey, label, params = {}) => {
  proxy.Confirm({
    message: `确定执行「${label}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api[apiKey], params, showLoading: true })
      if (!result) return
      const d = result.data
      if (d && typeof d.synced === 'number') {
        proxy.Message.success(`同步 ${d.synced} 条，跳过 ${d.skipped ?? 0} 条`)
      } else {
        proxy.Message.success('操作成功')
      }
    },
  })
}
</script>

<style lang="scss" scoped>
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-tool-group {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m-group-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--m-ink);
}

.tool-btn {
  height: 44px;
  border: 1px solid rgba(120, 120, 128, 0.24);
  border-radius: 13px;
  background: var(--m-ink);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: transform 0.15s, opacity 0.2s;

  &:active {
    transform: scale(0.98);
    opacity: 0.9;
  }

  &.warn {
    background: rgba(255, 149, 0, 0.14);
    border-color: rgba(255, 149, 0, 0.4);
    color: #b56a00;
  }

  &.danger {
    background: rgba(255, 59, 48, 0.1);
    border-color: rgba(255, 59, 48, 0.35);
    color: var(--m-danger);
  }
}

.tool-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--m-ink-3);
  line-height: 1.5;
}
</style>
