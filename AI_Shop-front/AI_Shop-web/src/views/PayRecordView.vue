<template>
  <div class="pay-record-page">
    <h2 class="page-title">支付记录</h2>
    <div v-if="loading && !list.length" class="loading-tip">加载中…</div>
    <div v-else-if="loadError && !list.length" class="load-error">
      <p>{{ loadError }}</p>
      <el-button type="primary" size="small" @click="retryLoad">重试</el-button>
    </div>
    <ul v-else-if="list.length" class="record-list">
      <li v-for="item in list" :key="item.tradeId" class="record-item card-flat">
        <div class="row">
          <span class="label">支付单号</span>
          <span class="value">{{ item.payOrderId }}</span>
        </div>
        <div class="row">
          <span class="label">金额</span>
          <span class="amount">¥{{ item.payAmount }}</span>
        </div>
        <div class="row">
          <span class="label">状态</span>
          <span :class="['status', statusClass(item.tradeStatus)]">{{ statusText(item.tradeStatus) }}</span>
        </div>
        <div class="row">
          <span class="label">时间</span>
          <span class="value">{{ formatTime(item.payTime || item.createTime) }}</span>
        </div>
      </li>
    </ul>
    <p v-else class="empty-tip">暂无支付记录</p>
    <div v-if="hasMore" class="load-more">
      <el-button text :loading="loadingMore" @click="loadMore">加载更多</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { payTradeApi } from '@/api/modules';

const list = ref<any[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const loadError = ref('');
const pageNo = ref(1);
const totalCount = ref(0);

const hasMore = computed(() => list.value.length < totalCount.value);

const statusText = (s: number) => {
  const map: Record<number, string> = { 0: '待支付', 1: '支付成功', 2: '已关闭', 3: '已退款' };
  return map[s] ?? '未知';
};

const statusClass = (s: number) => {
  if (s === 1) return 'ok';
  if (s === 0) return 'pending';
  return 'closed';
};

const formatTime = (t: string | number) => {
  if (!t) return '-';
  const d = new Date(t);
  return d.toLocaleString('zh-CN');
};

const load = async (append = false) => {
  if (!append) loadError.value = '';
  if (append) {
    loadingMore.value = true;
  } else {
    loading.value = true;
  }
  try {
    const res: any = await payTradeApi.loadMyTrades(pageNo.value);
    const rows = res?.list || [];
    totalCount.value = res?.totalCount ?? rows.length;
    list.value = append ? [...list.value, ...rows] : rows;
  } catch (e: any) {
    if (!append) {
      loadError.value = e?.info || e?.message || '支付记录加载失败，请稍后重试';
      list.value = [];
    }
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
};

const retryLoad = () => {
  pageNo.value = 1;
  void load();
};

const loadMore = async () => {
  if (!hasMore.value || loadingMore.value) return;
  pageNo.value += 1;
  await load(true);
};

onMounted(() => load());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pay-record-page {
  padding: 16px;
  max-width: 720px;
  margin: 0 auto;
}

.page-title {
  margin: 0 0 16px;
  font-size: 18px;
}

.record-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.record-item {
  padding: 14px;
}

.row {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  margin-bottom: 6px;

  &:last-child {
    margin-bottom: 0;
  }
}

.label {
  color: $color-text-secondary;
}

.amount {
  color: $color-primary;
  font-weight: 600;
}

.status.ok {
  color: #16a34a;
}

.status.pending {
  color: $color-primary;
}

.status.closed {
  color: $color-text-secondary;
}

.empty-tip,
.loading-tip,
.load-error {
  text-align: center;
  color: $color-text-secondary;
  padding: 40px 0;
}

.load-error p {
  margin: 0 0 12px;
}
</style>
