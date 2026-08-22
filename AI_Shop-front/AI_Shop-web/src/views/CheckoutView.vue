<template>
  <div class="checkout-page">
    <el-skeleton v-if="pageLoading" animated :rows="8" class="checkout-skeleton" />

    <div v-else-if="initError" class="checkout-error card-flat">
      <p>{{ initError }}</p>
      <el-button type="primary" round @click="init">重试</el-button>
    </div>

    <template v-else-if="items.length">
      <div v-if="isCouponRush" class="rush-pay-banner card-flat">
        <template v-if="payCountdownMs > 0">
          <p class="rush-pay-title">
            支付剩余 <strong>{{ payCountdownText }}</strong>
          </p>
          <p class="rush-pay-tip">订单已生成，超时将自动关闭。也可在「我的 → 我的订单 → 待付款」中继续支付。</p>
        </template>
        <p v-else class="rush-pay-expired">支付已超时，订单已关闭，请返回重新抢购。</p>
      </div>

      <section v-if="!isCouponRush" class="block card-flat checkout-address-block">
        <div class="block-head">
          <h3 class="block-title">收货地址</h3>
        </div>
        <p v-if="addressLoadError" class="block-error">
          {{ addressLoadError }}
          <button type="button" class="inline-retry" @click="loadAddresses">重试</button>
        </p>
        <div
          v-if="selectedAddress"
          class="checkout-address-picker is-filled"
          role="button"
          tabindex="0"
          @click="goSelectAddress"
          @keydown.enter.prevent="goSelectAddress"
        >
          <AddressCardBody :item="selectedAddress" />
          <span class="picker-change">更换</span>
        </div>
        <div v-else class="checkout-address-picker is-empty">
          <p class="empty-tip">请选择收货地址</p>
          <div class="empty-actions">
            <el-button type="primary" round size="small" @click="goSelectAddress">选择地址</el-button>
            <el-button round size="small" @click="goAddAddress">新增地址</el-button>
          </div>
        </div>
      </section>

      <section class="block card-flat">
        <div class="block-head">
          <h3 class="block-title">{{ isCouponRush ? '优惠券信息' : '商品清单' }}</h3>
          <span class="hint">共 {{ totalCount }} 件</span>
        </div>
        <ul class="goods-list">
          <li v-for="(item, index) in items" :key="`${item.productId}-${item.propertyValueIds}-${index}`" class="goods-item">
            <RouterLink v-if="!isCouponRush" :to="`/product/${item.productId}`" class="cover-wrap">
              <ProductImage :source="item.productCover" class="cover" />
            </RouterLink>
            <div v-else class="cover-wrap is-coupon">
              <el-icon class="coupon-icon"><Ticket /></el-icon>
            </div>
            <div class="goods-info">
              <RouterLink v-if="!isCouponRush" :to="`/product/${item.productId}`" class="goods-name">
                {{ item.productName }}
              </RouterLink>
              <p v-else class="goods-name">{{ item.productName }}</p>
              <p v-if="formatSkuText(item)" class="goods-sku">{{ formatSkuText(item) }}</p>
              <div class="goods-foot">
                <span class="unit-price">¥{{ Number(item.price).toFixed(2) }}</span>
                <span class="qty">×{{ item.buyCount }}</span>
                <span class="subtotal">小计 ¥{{ lineSubtotal(item).toFixed(2) }}</span>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <section v-if="!isCouponRush" class="block card-flat">
        <h3 class="block-title">订单备注</h3>
        <el-input
          v-model="remark"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
          placeholder="选填：配送、包装等要求"
        />
      </section>

      <section v-if="!isCouponRush" class="block card-flat">
        <div class="block-head">
          <h3 class="block-title">优惠券</h3>
          <button type="button" class="link-btn" @click="openCouponPicker">
            {{ selectedCouponLabel }}
          </button>
        </div>
        <p v-if="couponDiscount > 0" class="coupon-tip">已抵扣 ¥{{ couponDiscount.toFixed(2) }}</p>
        <p v-if="showMinPayTip" class="coupon-tip min-pay">使用优惠券后最低需支付 ¥{{ minPayAmountText }}</p>
        <p v-else-if="usableCoupons.some((c) => c.usable) && maxAvailableDiscount > 0" class="coupon-tip hint">
          您有可用优惠券，最高可抵扣 ¥{{ maxAvailableDiscount.toFixed(2) }}
        </p>
        <p v-if="couponLoadError" class="block-error">
          {{ couponLoadError }}
          <button type="button" class="inline-retry" @click="loadCoupons">重试</button>
        </p>
      </section>

      <section class="block card-flat">
        <h3 class="block-title">支付方式</h3>
        <el-radio-group v-model="payMethod" class="pay-methods">
          <div class="pay-option" :class="{ active: payMethod === PAY_METHOD_ALIPAY_WAP }">
            <el-radio :value="PAY_METHOD_ALIPAY_WAP">支付宝</el-radio>
            <p class="pay-desc">提交后将跳转支付宝完成手机支付</p>
          </div>
        </el-radio-group>
      </section>
    </template>

    <el-empty v-else description="没有待结算的商品">
      <el-button type="primary" round @click="router.push('/cart')">返回购物车</el-button>
    </el-empty>

    <LiquidGlassSurface
      v-if="items.length && !pageLoading"
      tag="footer"
      intensity="medium"
      class="checkout-bar ignore"
    >
      <div class="bar-summary">
        <span class="label">合计</span>
        <strong class="price-text">¥{{ payableAmount }}</strong>
        <span class="count">共 {{ totalCount }} 件</span>
      </div>
      <el-button
        type="primary"
        class="btn-submit"
        round
        :loading="submitting"
        :disabled="isCouponRush && payCountdownMs <= 0"
        @click="submit"
      >
        {{ submitButtonText }}
      </el-button>
    </LiquidGlassSurface>

    <el-dialog v-model="couponVisible" title="选择优惠券" width="92%" style="max-width: 520px">
      <div class="coupon-list">
        <button type="button" class="coupon-row" :class="{ active: !selectedUserCouponId }" @click="selectCoupon(null)">
          <div class="left">
            <p class="name">不使用优惠券</p>
            <p class="desc">本单不抵扣</p>
          </div>
          <span class="tag">默认</span>
        </button>
        <el-skeleton v-if="couponLoading" animated :rows="4" />
        <template v-else>
          <button
            v-for="c in usableCoupons"
            :key="c.userCouponId"
            type="button"
            class="coupon-row"
            :class="{ active: selectedUserCouponId === c.userCouponId, disabled: !c.usable }"
            :disabled="!c.usable"
            @click="selectCoupon(c)"
          >
            <div class="left">
              <p class="name">{{ c.couponName }}</p>
              <p class="desc">
                <template v-if="c.couponType === 2">折扣券 {{ Number(c.discountRate || 1) * 100 }}%</template>
                <template v-else>满减券</template>
                ·
                <span v-if="Number(c.thresholdAmount || 0) > 0">满 ¥{{ Number(c.thresholdAmount).toFixed(2) }} 可用</span>
                <span v-else>无门槛</span>
                · 有效期至 {{ formatCouponEnd(c.validEndTime) }}
              </p>
            </div>
            <div class="right">
              <p class="off">-¥{{ calcCouponDiscount(c).toFixed(2) }}</p>
              <span v-if="c.usable" class="tag">可用</span>
              <span v-else class="tag muted">不可用</span>
            </div>
          </button>
          <el-empty v-if="!usableCoupons.length" description="暂无可用优惠券" />
        </template>
      </div>
      <template #footer>
        <el-button @click="couponVisible = false">取消</el-button>
        <el-button type="primary" :disabled="couponLoading" @click="couponVisible = false">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { RouterLink, useRouter } from 'vue-router';
import { Ticket } from '@element-plus/icons-vue';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import AddressCardBody from '@/components/business/AddressCardBody.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { useCheckoutPage } from '@/composables/useCheckoutPage';

const router = useRouter();

const {
  pageLoading,
  initError,
  addressLoadError,
  couponLoadError,
  submitting,
  items,
  isCouponRush,
  payCountdownMs,
  payCountdownText,
  submitButtonText,
  remark,
  selectedAddress,
  payMethod,
  couponVisible,
  couponLoading,
  usableCoupons,
  selectedUserCouponId,
  couponDiscount,
  payableAmount,
  minPayAmountText,
  showMinPayTip,
  selectedCouponLabel,
  maxAvailableDiscount,
  totalCount,
  formatCouponEnd,
  calcCouponDiscount,
  formatSkuText,
  lineSubtotal,
  init,
  loadAddresses,
  goSelectAddress,
  goAddAddress,
  openCouponPicker,
  selectCoupon,
  loadCoupons,
  submit,
  PAY_METHOD_ALIPAY_WAP
} = useCheckoutPage('mobile');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.checkout-page {
  padding-bottom: calc(72px + env(safe-area-inset-bottom, 0));
}

.rush-pay-banner {
  margin-bottom: 10px;
  padding: 12px 14px;
  background: linear-gradient(135deg, rgba($color-primary, 0.12), rgba($color-primary, 0.04));
  border: 1px solid rgba($color-primary, 0.2);

  .rush-pay-title {
    margin: 0 0 6px;
    font-size: 14px;
    color: $color-text-title;

    strong {
      font-size: 18px;
      color: $color-primary;
      font-variant-numeric: tabular-nums;
    }
  }

  .rush-pay-tip {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: $color-text-muted;
  }

  .rush-pay-expired {
    margin: 0;
    font-size: 13px;
    color: $color-error;
  }
}

.checkout-skeleton {
  padding: 12px;
}

.checkout-error {
  margin: 16px;
  padding: 24px 16px;
  text-align: center;

  p {
    margin: 0 0 12px;
    font-size: 14px;
    color: $color-text-secondary;
  }
}

.block-error {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.5;
  color: $color-error;
}

.inline-retry {
  margin-left: 6px;
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: $color-primary;
  cursor: pointer;
  text-decoration: underline;
}

.block {
  padding: 14px 12px;
  margin-bottom: 10px;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.block-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-title;
}

.link-btn {
  border: none;
  background: none;
  font-size: 13px;
  color: $color-primary;
  cursor: pointer;
  padding: 0;
}

.coupon-tip {
  margin: 8px 0 0;
  font-size: 12px;
  color: $color-price;

  &.hint {
    color: $color-primary;
  }

  &.min-pay {
    color: $color-text-muted;
    line-height: 1.5;
  }
}

.coupon-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.coupon-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px;
  border: 1px solid $color-border;
  border-radius: $radius-card;
  background: $color-card;
  text-align: left;

  &.active {
    border-color: rgba($color-primary, 0.6);
    background: $color-primary-soft;
  }

  &.disabled {
    opacity: 0.55;
  }

  .left {
    flex: 1;
    min-width: 0;
  }

  .name {
    margin: 0 0 4px;
    font-size: 13px;
    font-weight: 600;
    color: $color-text-title;
  }

  .desc {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
    line-height: 1.35;
  }

  .right {
    flex-shrink: 0;
    text-align: right;
  }

  .off {
    margin: 0 0 4px;
    font-size: 14px;
    font-weight: 700;
    color: $color-price;
  }

  .tag {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: $radius-pill;
    color: $color-primary;
    background: rgba($color-primary, 0.1);
  }

  .tag.muted {
    color: $color-text-muted;
    background: $color-bg-subtle;
  }
}

.hint {
  font-size: 12px;
  color: $color-text-muted;
}

.checkout-address-picker {
  border: 1px solid $color-border;
  border-radius: $radius-card;
  background: #fafafa;

  &.is-filled {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 12px;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;

    &:active {
      background: #fffaf7;
      border-color: rgba($color-primary, 0.35);
    }

    :deep(.card-main) {
      flex: 1;
      min-width: 0;
    }
  }

  &.is-empty {
    padding: 16px 12px;
    text-align: center;

    .empty-tip {
      margin: 0 0 12px;
      font-size: 13px;
      color: $color-text-muted;
    }

    .empty-actions {
      display: flex;
      justify-content: center;
      gap: 10px;
      flex-wrap: wrap;
    }
  }

  .picker-change {
    flex-shrink: 0;
    font-size: 13px;
    color: $color-primary;
    padding-top: 2px;
  }
}

.goods-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.goods-item {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid $color-border-light;

  &:last-child {
    border-bottom: none;
  }
}

.cover-wrap {
  flex-shrink: 0;
  width: 72px;
  height: 72px;
  border-radius: $radius-card;
  overflow: hidden;
  background: $color-bg-subtle;

  &.is-coupon {
    display: grid;
    place-items: center;
    color: $color-primary;
  }
}

.goods-info {
  flex: 1;
  min-width: 0;
}

.goods-name {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 500;
  color: $color-text-title;
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.goods-sku {
  margin: 0 0 6px;
  font-size: 12px;
  color: $color-text-muted;
}

.goods-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;

  .unit-price {
    color: $color-price;
    font-weight: 600;
  }

  .qty {
    color: $color-text-muted;
  }

  .subtotal {
    margin-left: auto;
    color: $color-text-title;
    font-weight: 500;
  }
}

.pay-methods {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.pay-option {
  padding: 12px;
  border: 1px solid $color-border;
  border-radius: $radius-card;

  &.active {
    border-color: rgba($color-primary, 0.45);
    background: rgba($color-primary, 0.04);
  }

  .pay-desc {
    margin: 4px 0 0 24px;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.checkout-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 100;
  border-top: 1px solid var(--glass-border-soft);
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.08);

  :deep(.liquid-glass-surface__content) {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    padding-bottom: calc(10px + env(safe-area-inset-bottom, 0));
    width: 100%;
  }

  .bar-summary {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px;

    .label {
      font-size: 13px;
      color: $color-text-muted;
    }

    .price-text {
      font-size: 20px;
      color: $color-price;
    }

    .count {
      font-size: 12px;
      color: $color-text-muted;
    }
  }

  .btn-submit {
    flex-shrink: 0;
    min-width: 120px;
    height: 44px;
    font-weight: 600;
  }
}
</style>
