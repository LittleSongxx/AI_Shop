<template>
  <div class="action-confirm-card" :class="statusClass">
    <header class="card-head">
      <p class="card-title">{{ card.label || '待确认操作' }}</p>
      <span v-if="isPending" class="card-badge">待确认</span>
    </header>

    <p class="card-hint">{{ cardHint }}</p>

    <div v-if="card.orderId" class="order-meta">
      <span class="order-id" :title="card.orderId">订单号 {{ shortOrderId }}</span>
      <span v-if="orderAmountText" class="order-amount">{{ orderAmountText }}</span>
    </div>

    <ul v-if="orderItems.length" class="item-list">
      <li v-for="item in orderItems" :key="item.orderItemId || item.productId" class="item-row">
        <div class="item-cover" :class="{ 'is-coupon': isCouponOrder }">
          <el-icon v-if="isCouponOrder" class="coupon-icon"><Ticket /></el-icon>
          <ProductImage v-else :source="item.cover" width="52" height="52" />
        </div>
        <div class="item-info">
          <p class="item-name">{{ item.productName }}</p>
          <p v-if="item.propertyInfo && !isCouponOrder" class="item-sku">{{ item.propertyInfo }}</p>
          <p v-if="item.orderItemId" class="item-id" :title="item.orderItemId">
            订单项 ID {{ item.orderItemId }}
          </p>
          <p v-if="itemMeta(item)" class="item-meta">{{ itemMeta(item) }}</p>
        </div>
      </li>
    </ul>

    <dl v-if="detailRows.length" class="detail-list">
      <div v-for="row in detailRows" :key="row.label" class="detail-row">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
    <p v-else-if="card.summary && !orderItems.length" class="summary-fallback">{{ card.summary }}</p>

    <p v-if="card.riskTip" class="risk-tip">{{ card.riskTip }}</p>

    <p v-if="resultMessage" class="result-msg" :class="{ success: resultSuccess, error: !resultSuccess }">
      {{ resultMessage }}
    </p>

    <footer v-if="showActions" class="actions">
      <button type="button" class="btn-cancel" :disabled="loading" @click="onCancel">取消</button>
      <button type="button" class="btn-confirm" :disabled="loading" @click="onConfirm">
        {{ loading ? '处理中…' : card.confirmText || '确认提交' }}
      </button>
    </footer>
    <p v-else-if="statusLabel" class="status-label">{{ statusLabel }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Ticket } from '@element-plus/icons-vue';
import { agentApi } from '@/api/modules';
import ProductImage from '@/components/common/ProductImage.vue';
import { toast } from '@/utils/toast';

export interface ActionConfirmDetailRow {
  label: string;
  value: string;
}

export interface ActionConfirmOrderItem {
  orderItemId?: string;
  productId?: string;
  productName?: string;
  cover?: string;
  propertyInfo?: string;
  itemAmount?: number | string;
  buyCount?: number | string;
}

export interface ActionConfirmCardData {
  type?: string;
  token?: string;
  actionType?: string;
  label?: string;
  summary?: string;
  confirmText?: string;
  riskTip?: string;
  intro?: string;
  status?: number | string;
  orderId?: string;
  orderAmount?: number | string;
  payScene?: string | number;
  items?: ActionConfirmOrderItem[];
  details?: ActionConfirmDetailRow[];
}

const props = defineProps<{
  card: ActionConfirmCardData;
}>();

const emit = defineEmits<{
  updated: [card: ActionConfirmCardData];
}>();

const PENDING = 0;
const CONFIRMED = 1;
const CANCELLED = 2;
const EXPIRED = 3;

const loading = ref(false);
const localStatus = ref<number | null>(null);
const resultMessage = ref('');
const resultSuccess = ref(false);

const effectiveStatus = computed(() => {
  const raw = localStatus.value ?? props.card.status ?? PENDING;
  const num = Number(raw);
  return Number.isFinite(num) ? num : PENDING;
});

const isPending = computed(() => effectiveStatus.value === PENDING);

const showActions = computed(() => isPending.value && !resultMessage.value);

const statusClass = computed(() => {
  if (effectiveStatus.value === CONFIRMED) return 'is-confirmed';
  if (effectiveStatus.value === CANCELLED) return 'is-cancelled';
  if (effectiveStatus.value === EXPIRED) return 'is-expired';
  return 'is-pending';
});

const statusLabel = computed(() => {
  if (effectiveStatus.value === CONFIRMED) return '已确认执行';
  if (effectiveStatus.value === CANCELLED) return '已取消';
  if (effectiveStatus.value === EXPIRED) return '已过期，请重新发起';
  return '';
});

const cardHint = computed(() => {
  const intro = (props.card.intro || '').trim();
  if (!intro || intro.length > 100) {
    return '请核对以下信息，确认后将立即执行。';
  }
  return intro;
});

const detailRows = computed(() => {
  const rows = props.card.details;
  if (!Array.isArray(rows)) return [];
  return rows
    .map((row) => ({
      label: row?.label != null ? String(row.label).trim() : '',
      value: row?.value != null ? String(row.value).trim() : ''
    }))
    .filter((row) => row.label && row.value);
});

const orderItems = computed(() => {
  const items = props.card.items;
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => ({
      orderItemId: item?.orderItemId != null ? String(item.orderItemId) : undefined,
      productId: item?.productId != null ? String(item.productId) : undefined,
      productName: item?.productName != null ? String(item.productName).trim() : '',
      cover: item?.cover != null ? String(item.cover) : undefined,
      propertyInfo: item?.propertyInfo != null ? String(item.propertyInfo).trim() : undefined,
      itemAmount: item?.itemAmount,
      buyCount: item?.buyCount
    }))
    .filter((item) => item.productName);
});

const COUPON_ORDER_PAY_SCENE = '2';
const COUPON_ORDER_PROPERTY = '优惠券秒杀';

const isCouponOrder = computed(() => {
  if (String(props.card.payScene) === COUPON_ORDER_PAY_SCENE) {
    return true;
  }
  return orderItems.value.some((item) => item.propertyInfo === COUPON_ORDER_PROPERTY);
});

const shortOrderId = computed(() => {
  const id = props.card.orderId || '';
  if (id.length <= 18) return id;
  return `${id.slice(0, 10)}…${id.slice(-6)}`;
});

const orderAmountText = computed(() => {
  const amount = props.card.orderAmount;
  if (amount == null || amount === '') return '';
  const hasDetailAmount = detailRows.value.some((row) => row.label.includes('金额'));
  if (hasDetailAmount) return '';
  return `¥${amount}`;
});

const itemMeta = (item: ActionConfirmOrderItem) => {
  const parts: string[] = [];
  if (item.buyCount != null && item.buyCount !== '') {
    parts.push(`×${item.buyCount}`);
  }
  if (item.itemAmount != null && item.itemAmount !== '') {
    parts.push(`¥${item.itemAmount}`);
  }
  return parts.join('  ');
};

const patchCard = (status: number) => {
  localStatus.value = status;
  emit('updated', { ...props.card, status });
};

const onConfirm = async () => {
  if (!props.card.token || loading.value) return;
  loading.value = true;
  try {
    const res = await agentApi.confirmAction(props.card.token);
    const data = res as { success?: boolean; resultMessage?: string; actionType?: string };
    resultSuccess.value = !!data?.success;
    resultMessage.value = data?.resultMessage || (data?.success ? '操作成功' : '操作失败');
    if (data?.success) {
      patchCard(CONFIRMED);
      toast.success(resultMessage.value);
    } else {
      toast.error(resultMessage.value);
    }
  } catch {
    toast.error('确认失败，请稍后重试');
  } finally {
    loading.value = false;
  }
};

const onCancel = async () => {
  if (!props.card.token || loading.value) return;
  loading.value = true;
  try {
    await agentApi.cancelAction(props.card.token);
    patchCard(CANCELLED);
    resultMessage.value = '已取消操作';
    resultSuccess.value = false;
  } catch {
    toast.error('取消失败，请稍后重试');
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.action-confirm-card {
  width: 100%;
  min-width: 240px;
  max-width: 100%;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid rgba($color-primary, 0.28);
  background: #fff;
  box-sizing: border-box;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.card-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: $color-text-title;
}

.card-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 1.4;
  color: $color-primary;
  background: rgba($color-primary, 0.1);
}

.card-hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.45;
  color: $color-text-muted;
}

.order-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  min-width: 0;
}

.order-id {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  line-height: 1.35;
  color: $color-text-muted;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.order-amount {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: $color-primary;
}

.item-list {
  list-style: none;
  margin: 0 0 10px;
  padding: 8px;
  border-radius: 8px;
  background: $color-bg-subtle;
}

.item-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;

  & + & {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed rgba($color-text-muted, 0.2);
  }
}

.item-cover {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;

  &.is-coupon {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, rgba($color-primary, 0.12), rgba($color-price, 0.1));

    .coupon-icon {
      font-size: 28px;
      color: $color-primary;
    }
  }
}

.item-info {
  flex: 1;
  min-width: 0;
}

.item-name {
  margin: 0;
  font-size: 13px;
  line-height: 1.4;
  font-weight: 500;
  color: $color-text-title;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.item-sku {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.35;
  color: $color-text-muted;
}

.item-id {
  margin: 4px 0 0;
  font-size: 10px;
  line-height: 1.35;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: $color-text-muted;
  word-break: break-all;
}

.item-meta {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.35;
  color: $color-text-body;
}

.detail-list {
  margin: 0 0 10px;
  padding: 10px;
  border-radius: 8px;
  background: $color-bg-subtle;
}

.detail-row {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px;
  font-size: 13px;
  line-height: 1.45;

  & + & {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed rgba($color-text-muted, 0.25);
  }
}

.detail-row dt {
  margin: 0;
  color: $color-text-muted;
  word-break: keep-all;
}

.detail-row dd {
  margin: 0;
  color: $color-text-title;
  font-weight: 500;
  word-break: break-all;
}

.summary-fallback {
  margin: 0 0 10px;
  padding: 10px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.45;
  color: $color-text-title;
  background: $color-bg-subtle;
}

.risk-tip {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.4;
  color: #b45309;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid rgba($color-text-muted, 0.15);
}

.btn-cancel,
.btn-confirm {
  border: none;
  border-radius: 8px;
  min-width: 72px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.btn-cancel {
  background: $color-bg-subtle;
  color: $color-text-body;

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.btn-confirm {
  background: $color-primary;
  color: #fff;

  &:disabled {
    opacity: 0.7;
    cursor: not-allowed;
  }
}

.result-msg {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.45;

  &.success {
    color: #16a34a;
  }

  &.error {
    color: #dc2626;
  }
}

.status-label {
  margin: 0;
  padding-top: 8px;
  font-size: 12px;
  color: $color-text-muted;
  border-top: 1px solid rgba($color-text-muted, 0.15);
}

.is-confirmed {
  border-color: rgba(#16a34a, 0.35);
}

.is-cancelled,
.is-expired {
  border-color: rgba($color-text-muted, 0.3);
  opacity: 0.92;
}
</style>
