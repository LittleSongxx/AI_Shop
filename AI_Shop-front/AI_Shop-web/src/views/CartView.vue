<template>
  <div class="cart-page product-page-compact">
    <el-skeleton v-if="pageLoading" animated :rows="6" class="cart-skeleton" />

    <div v-else-if="loadError" class="cart-error card-flat">
      <p>{{ loadError }}</p>
      <el-button type="primary" round @click="load">重试</el-button>
    </div>

    <div v-else-if="list.length" class="cart-list">
      <SwipeDeleteRow
        v-for="row in list"
        :key="row.cartId"
        :open="openSwipeId === row.cartId"
        @open="openSwipeId = row.cartId"
        @close="onSwipeClose(row.cartId)"
        @delete="del(row.cartId)"
      >
      <article class="cart-item">
        <el-checkbox
          class="item-check"
          :model-value="selectedIds.has(row.cartId)"
          :disabled="!row.productOnSale"
          @change="onItemCheckChange(row.cartId, $event)"
        />
        <RouterLink :to="`/product/${row.productId}`" class="item-cover-col">
          <ProductImage :source="row.productCover" class="item-cover-img" />
        </RouterLink>
        <div class="item-body">
          <RouterLink :to="`/product/${row.productId}`" class="item-name" :title="row.productName">
            {{ row.productName }}
          </RouterLink>
          <p v-if="row.propertyData?.length" class="item-sku">
            <span v-for="(p, i) in row.propertyData" :key="i">
              {{ p.propertyName }}：{{ p.propertyValue }}
            </span>
          </p>
          <p v-if="!row.productOnSale" class="item-off">已下架</p>
          <div class="item-foot">
            <div class="price-block">
              <span class="unit-price">¥{{ formatUnitPrice(row) }}</span>
              <span v-if="priceDeltaText(row)" class="price-delta" :class="priceDeltaClass(row)">
                {{ priceDeltaText(row) }}
              </span>
              <span class="line-total">小计 ¥{{ formatLineTotal(row) }}</span>
            </div>
            <div
              class="qty-stepper"
              :class="{ disabled: !row.productOnSale || updatingCartId === row.cartId }"
              @click.stop
            >
              <button
                type="button"
                class="qty-btn"
                aria-label="减少数量"
                :disabled="!row.productOnSale || updatingCartId === row.cartId || getQty(row) <= 1"
                @click="decreaseQty(row)"
              >
                −
              </button>
              <input
                v-model.number="row.buyCount"
                class="qty-input"
                type="number"
                inputmode="numeric"
                min="1"
                :max="MAX_CART_QTY"
                aria-label="数量"
                :disabled="!row.productOnSale || updatingCartId === row.cartId"
                @focus="onQtyFocus(row)"
                @blur="commitQtyInput(row)"
                @keydown.enter="($event.target as HTMLInputElement).blur()"
              />
              <button
                type="button"
                class="qty-btn"
                aria-label="增加数量"
                :disabled="!row.productOnSale || updatingCartId === row.cartId || getQty(row) >= MAX_CART_QTY"
                @click="increaseQty(row)"
              >
                +
              </button>
            </div>
          </div>
        </div>
      </article>
      </SwipeDeleteRow>
    </div>

    <el-empty v-else-if="!pageLoading" description="购物车空空如也，去逛逛吧" class="cart-empty">
      <el-button type="primary" round @click="router.push('/')">去首页</el-button>
    </el-empty>

    <section v-if="recommendProducts.length" class="cart-recommend">
      <header class="recommend-head">
        <span class="recommend-title">为你推荐</span>
        <span class="recommend-sub">猜你喜欢 · 更多好物</span>
      </header>
      <div :class="isDesktop ? 'pc-cart-recommend-grid' : 'recommend-grid'">
        <template v-if="isDesktop">
          <PcProductTile
            v-for="p in recommendProducts"
            :key="p.productId"
            :product="p"
            @click="goProduct"
          />
        </template>
        <template v-else>
          <ProductCard
            v-for="p in recommendProducts"
            :key="p.productId"
            :product="p"
            compact
            @click="goProduct"
          />
        </template>
      </div>
    </section>

    <LiquidGlassSurface v-if="list.length && !pageLoading && !loadError" tag="footer" intensity="strong" class="cart-bar ignore">
      <el-checkbox
        class="bar-check-all"
        :model-value="allSelectableChecked"
        :indeterminate="isIndeterminate"
        @change="toggleAll"
      >
        全选
      </el-checkbox>
      <span class="bar-count">已选 {{ selectedCount }} 件</span>
      <div class="bar-right">
        <span class="bar-amount">
          合计：<strong>¥{{ selectedAmount }}</strong>
        </span>
        <el-button
          type="primary"
          size="large"
          round
          class="btn-checkout"
          :disabled="selectedCount === 0"
          @click="checkout"
        >
          结算
        </el-button>
      </div>
    </LiquidGlassSurface>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import SwipeDeleteRow from '@/components/business/SwipeDeleteRow.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import ProductCard from '@/components/business/ProductCard.vue';
import PcProductTile from '@/components/pc/PcProductTile.vue';
import { useDevice } from '@/composables/useDevice';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import { saveCheckoutSession, type CheckoutLineItem } from '@/utils/checkout';
import { MAX_CART_QTY } from '@/constants/validation';
import { cartApi, productApi } from '@/api/modules';
import { filterOnSaleProducts } from '@/utils/product';
import { useCartStore } from '@/stores/cart';
import { useAuthStore } from '@/stores/auth';
import { usePageRefresh } from '@/composables/pullRefresh';

const router = useRouter();
const cartStore = useCartStore();
const authStore = useAuthStore();
const { isDesktop } = useDevice();
const list = ref<any[]>([]);
const pageLoading = ref(true);
const loadError = ref('');
const recommendProducts = ref<any[]>([]);
const selectedIds = ref<Set<string>>(new Set());
const updatingCartId = ref<string | null>(null);
const openSwipeId = ref<string | null>(null);

const goProduct = (p: any) => {
  if (p?.productId) router.push(`/product/${p.productId}`);
};

const loadRecommend = async () => {
  if (recommendProducts.value.length) return;
  try {
    const commend = await productApi.loadCommendProduct();
    const arr = Array.isArray(commend) ? commend : commend?.list;
    const filtered = filterOnSaleProducts(arr);
    const targetCount = isDesktop.value ? 12 : 6;
    if (isDesktop.value && filtered.length < targetCount && filtered.length > 0) {

      const items = [...filtered];
      while (items.length < targetCount) {
        items.push(filtered[items.length % filtered.length]);
      }
      recommendProducts.value = items;
    } else {
      recommendProducts.value = filtered.slice(0, targetCount);
    }
  } catch {

  }
};

const onSwipeClose = (cartId: string) => {
  if (openSwipeId.value === cartId) openSwipeId.value = null;
};

const qtySnapshot = new Map<string, number>();

const getQty = (row: { buyCount?: number }) => Math.max(1, Number(row.buyCount) || 1);

const formatUnitPrice = (row: { price?: number }) => Number(row.price || 0).toFixed(2);

const formatLineTotal = (row: { price?: number; buyCount?: number }) =>
  (Number(row.price || 0) * getQty(row)).toFixed(2);

const priceDiff = (row: { price?: number; addPrice?: number }) => {
  if (row.addPrice == null || row.price == null) return 0;
  return Number(row.price) - Number(row.addPrice);
};

const priceDeltaText = (row: { price?: number; addPrice?: number }) => {
  const diff = priceDiff(row);
  if (Math.abs(diff) < 0.005) return '';
  const abs = Math.abs(diff).toFixed(2);
  return diff > 0 ? `比添加时多了${abs}元` : `比添加时少了${abs}元`;
};

const priceDeltaClass = (row: { price?: number; addPrice?: number }) => {
  const diff = priceDiff(row);
  if (diff > 0) return 'up';
  if (diff < 0) return 'down';
  return '';
};

const normalizeCartRow = (row: Record<string, unknown>) => ({
  ...row,
  buyCount: Math.max(1, Math.floor(Number(row.buyCount) || 1))
});

const selectableItems = computed(() => list.value.filter((row) => row.productOnSale));

const selectedCount = computed(() =>
  selectableItems.value
    .filter((row) => selectedIds.value.has(row.cartId))
    .reduce((sum, row) => sum + (Number(row.buyCount) || 0), 0)
);

const selectedAmount = computed(() => {
  const total = selectableItems.value
    .filter((row) => selectedIds.value.has(row.cartId))
    .reduce((sum, row) => sum + Number(row.price) * (Number(row.buyCount) || 0), 0);
  return total.toFixed(2);
});

const allSelectableChecked = computed(
  () =>
    selectableItems.value.length > 0 &&
    selectableItems.value.every((row) => selectedIds.value.has(row.cartId))
);

const isIndeterminate = computed(() => {
  const selected = selectableItems.value.filter((row) => selectedIds.value.has(row.cartId)).length;
  return selected > 0 && selected < selectableItems.value.length;
});

const syncDefaultSelection = () => {
  selectedIds.value = new Set(selectableItems.value.map((row) => row.cartId));
};

const onItemCheckChange = (cartId: string, checked: boolean | string | number) => {
  toggleItem(cartId, !!checked);
};

const toggleItem = (cartId: string, checked: boolean) => {
  const next = new Set(selectedIds.value);
  if (checked) next.add(cartId);
  else next.delete(cartId);
  selectedIds.value = next;
};

const toggleAll = (checked: boolean) => {
  if (checked) syncDefaultSelection();
  else selectedIds.value = new Set();
};

const load = async () => {
  pageLoading.value = true;
  loadError.value = '';
  try {
    const ok = await authStore.ensureSession();
    if (!ok) {
      list.value = [];
      selectedIds.value = new Set();
      cartStore.resetCart();
      return;
    }

    const res = await cartApi.loadProductCart({ pageNo: 1 });
    const rows = Array.isArray(res?.list) ? res.list : [];
    list.value = rows.map((row: Record<string, unknown>) => normalizeCartRow(row));
    syncDefaultSelection();
    await cartStore.fetchCartCount();
  } catch (e: any) {
    loadError.value = e?.info || '购物车加载失败，请重试';
    list.value = [];
    selectedIds.value = new Set();
  } finally {
    pageLoading.value = false;
  }
};

watch(
  () => authStore.userInfo?.userId,
  (userId, prevUserId) => {
    if (userId !== prevUserId) {
      list.value = [];
      selectedIds.value = new Set();
      if (userId) void load();
      else cartStore.resetCart();
    }
  }
);

const decreaseQty = (row: Record<string, any>) => {
  void changeQty(row, getQty(row) - 1);
};

const increaseQty = (row: Record<string, any>) => {
  void changeQty(row, getQty(row) + 1);
};

const onQtyFocus = (row: Record<string, any>) => {
  qtySnapshot.set(String(row.cartId), getQty(row));
};

const commitQtyInput = (row: Record<string, any>) => {
  const cartId = String(row.cartId);
  const prev = qtySnapshot.get(cartId) ?? getQty(row);
  qtySnapshot.delete(cartId);

  let count = Math.floor(Number(row.buyCount));
  if (!Number.isFinite(count) || count < 1) {
    row.buyCount = prev;
    return;
  }
  if (count > MAX_CART_QTY) {
    count = MAX_CART_QTY;
    toast.warning(`单个商品最多购买 ${MAX_CART_QTY} 件`);
  }
  row.buyCount = count;
  void changeQty(row, count, prev);
};

const changeQty = async (
  row: Record<string, any>,
  val: number | undefined,
  prevOverride?: number
) => {
  if (!row.productOnSale || updatingCartId.value) return;

  const prev = prevOverride ?? (Number(row.buyCount) || 1);
  let count = Math.floor(Number(val) || 0);

  if (count < 1) count = 1;
  if (count > MAX_CART_QTY) {
    count = MAX_CART_QTY;
    if (prev < MAX_CART_QTY) toast.warning(`单个商品最多购买 ${MAX_CART_QTY} 件`);
  }
  if (count === prev) {
    row.buyCount = count;
    return;
  }

  const delta = count - prev;
  if (delta === 0) return;

  updatingCartId.value = row.cartId;
  try {
    await cartApi.add2Cart({
      productId: row.productId,
      propertyValueIds: row.propertyValueIds,
      buyCount: delta
    });
    row.buyCount = prev + delta;
    await cartStore.fetchCartCount();
  } catch {
    row.buyCount = prev;
  } finally {
    updatingCartId.value = null;
  }
};

const del = async (id: string) => {
  const ok = await confirmAction('确定要将该商品移出购物车吗？', {
    title: '移出购物车',
    confirmButtonText: '移出'
  });
  if (!ok) return;
  await cartApi.deleteCart(id);
  if (openSwipeId.value === id) openSwipeId.value = null;
  toast.success('已移出购物车');
  await load();
};

const checkout = () => {
  const rows = selectableItems.value.filter((row) => selectedIds.value.has(row.cartId));
  if (!rows.length) {
    toast.warning('请先勾选要结算的商品');
    return;
  }
  const items: CheckoutLineItem[] = rows.map((row) => ({
    cartId: row.cartId,
    productId: row.productId,
    productName: row.productName,
    productCover: row.productCover,
    propertyValueIds: row.propertyValueIds,
    propertyValueIdHash: row.propertyValueIdHash,
    propertyData: row.propertyData,
    price: Number(row.price),
    buyCount: Number(row.buyCount) || 1
  }));
  saveCheckoutSession(items, 1);
  router.push('/checkout');
};

onMounted(() => {
  load();
  loadRecommend();
});
usePageRefresh(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.cart-page {
  min-height: 100%;
  background: transparent;
  padding-bottom: calc(var(--mobile-tab-stack-height, #{$mobile-tab-height + 12px}) + 68px);
}

.cart-skeleton {
  padding: 12px;
}

.cart-error {
  margin: 16px;
  padding: 24px 16px;
  text-align: center;

  p {
    margin: 0 0 12px;
    font-size: 14px;
    color: $color-text-secondary;
  }
}

.cart-list {
  display: flex;
  flex-direction: column;
  gap: 10px;

  :deep(.swipe-delete-row) {
    border-radius: $radius-card;
    overflow: hidden;
    box-shadow: $shadow-card;
  }

  :deep(.swipe-content) {
    border: none;
    border-radius: $radius-card;
    box-shadow: none;
    border-bottom: none;
  }
}

.cart-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 12px 12px 8px;
  margin: 0;
  border: none;
  box-shadow: none;
  background: transparent;
}

.item-check {
  flex-shrink: 0;
  margin-top: 24px;
  height: auto;
}

.item-cover-col {
  flex: 0 0 25%;
  width: 25%;
  max-width: 76px;
  aspect-ratio: 1;
  border-radius: 12px;
  overflow: hidden;

  :deep(.product-image) {
    width: 100% !important;
    height: 100% !important;
    border-radius: 12px;
  }
}

.item-body {
  flex: 1;
  min-width: 0;
}

.item-name {
  display: -webkit-box;
  font-size: 12px;
  font-weight: 500;
  color: $color-text-title;
  text-decoration: none;
  line-height: 1.35;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;

  &:hover {
    color: $color-primary;
  }
}

.item-sku {
  margin: 6px 0 0;
  font-size: 12px;
  color: $color-text-muted;

  span + span::before {
    content: ' · ';
  }
}

.item-off {
  margin: 4px 0 0;
  font-size: 12px;
  color: $color-price;
}

.item-foot {
  margin-top: 10px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}

.price-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;

  .unit-price {
    color: $color-price;
    font-size: 15px;
    font-weight: 700;
    line-height: 1.2;
  }

  .price-delta {
    font-size: 11px;
    font-weight: 500;
    line-height: 1.2;

    &.up {
      color: $color-error;
    }

    &.down {
      color: $color-success;
    }
  }

  .line-total {
    font-size: 12px;
    font-weight: 600;
    color: $color-text-title;
    line-height: 1.2;
  }
}

.qty-stepper {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  height: 30px;
  border: 1px solid $color-border;
  border-radius: $radius-sm;
  background: $color-card;
  overflow: hidden;

  &.disabled {
    opacity: 0.55;
  }
}

.qty-btn {
  width: 32px;
  height: 100%;
  padding: 0;
  border: none;
  background: $color-bg-subtle;
  color: $color-text-title;
  font-size: 18px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: background $transition-fast, color $transition-fast;

  &:hover:not(:disabled) {
    background: $color-primary-muted;
    color: $color-primary;
  }

  &:disabled {
    cursor: not-allowed;
    color: $color-text-muted;
    background: $color-bg-subtle;
  }
}

.qty-input {
  width: 40px;
  min-width: 36px;
  max-width: 56px;
  padding: 0 4px;
  border: none;
  border-left: 1px solid $color-border;
  border-right: 1px solid $color-border;
  background: transparent;
  font-size: 14px;
  font-weight: 600;
  color: $color-text-title;
  text-align: center;
  -moz-appearance: textfield;
  appearance: textfield;

  &::-webkit-outer-spin-button,
  &::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }

  &:focus {
    outline: none;
    background: $color-bg-subtle;
  }

  &:disabled {
    cursor: not-allowed;
    color: $color-text-muted;
  }
}

.cart-empty {
  padding: 48px 0;
}

.cart-recommend {
  margin-top: 14px;
  padding-bottom: 16px;
}

.recommend-head {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 10px;
  margin-bottom: 12px;

  &::before,
  &::after {
    content: '';
    flex: 0 0 28px;
    height: 1px;
    align-self: center;
    background: linear-gradient(90deg, transparent, $color-border-gray);
  }

  &::after {
    background: linear-gradient(90deg, $color-border-gray, transparent);
  }

  .recommend-title {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: $letter-spacing-heading;
    color: $color-text-title;
  }

  .recommend-sub {
    font-size: 11px;
    color: $color-text-muted;
  }
}

.recommend-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.cart-bar {
  position: fixed;
  left: 12px;
  right: 12px;
  bottom: calc(var(--mobile-tab-stack-height, #{$mobile-tab-height + 12px}) + 6px);
  z-index: 1000;
  border: none;
  border-radius: 22px;
  overflow: hidden;
  box-shadow:
    0 6px 6px rgba(0, 0, 0, 0.16),
    0 0 20px rgba(0, 0, 0, 0.08);

  :deep(.liquid-glass-surface__content) {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    width: 100%;
  }

  @media (min-width: $breakpoint-mobile) {
    position: sticky;
    bottom: 0;
    margin-top: 16px;
  }

  .bar-check-all {
    flex-shrink: 0;

    :deep(.el-checkbox__label) {
      font-size: 13px;
      font-weight: 500;
      color: $color-text-body;
      padding-left: 6px;
    }
  }

  .bar-count {
    flex: 0 1 auto;
    min-width: 0;
    font-size: 11px;
    font-weight: 400;
    line-height: 1.2;
    color: $color-text-muted;
    white-space: nowrap;
  }

  .bar-right {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    margin-left: auto;
  }

  .bar-amount {
    font-size: 12px;
    color: $color-text-body;
    white-space: nowrap;

    strong {
      color: $color-price;
      font-size: 18px;
      font-weight: 700;
    }
  }

  .btn-checkout {
    flex-shrink: 0;
    min-width: 96px;
    height: 40px;
    padding: 0 20px;
    font-weight: 600;
    font-size: 15px;
    border: none;
    border-radius: $radius-pill;
    background: linear-gradient(90deg, $color-primary-hover, $color-primary);
  }
}
</style>
