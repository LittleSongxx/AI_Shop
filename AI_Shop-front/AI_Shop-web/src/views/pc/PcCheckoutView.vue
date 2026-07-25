<template>
  <div class="pc-checkout ignore">
    <nav class="pc-checkout-steps" aria-label="结账流程">
      <RouterLink to="/cart" class="step-link">购物车</RouterLink>
      <span class="step-sep" aria-hidden="true" />
      <span class="step-current">确认订单</span>
      <span class="step-sep" aria-hidden="true" />
      <span class="step-muted">支付</span>
    </nav>

    <el-skeleton v-if="pageLoading" animated :rows="10" class="pc-checkout-skeleton" />

    <section v-else-if="initError" class="pc-checkout-state">
      <p>{{ initError }}</p>
      <el-button type="primary" @click="init">重试</el-button>
    </section>

    <template v-else-if="items.length">
      <div v-if="isCouponRush" class="pc-rush-banner">
        <template v-if="payCountdownMs > 0">
          <p class="rush-title">
            支付剩余 <strong>{{ payCountdownText }}</strong>
          </p>
          <p class="rush-tip">订单已生成，超时将自动关闭。也可在「我的订单 → 待付款」中继续支付。</p>
        </template>
        <p v-else class="rush-expired">支付已超时，订单已关闭，请返回重新抢购。</p>
      </div>

      <div class="pc-checkout-layout">
        <div class="pc-checkout-main">
          <section v-if="!isCouponRush" class="pc-panel">
            <header class="panel-head">
              <h2>收货地址</h2>
              <button type="button" class="text-link" @click="openAddressForm()">新增地址</button>
            </header>
            <p v-if="addressLoadError" class="panel-error">
              {{ addressLoadError }}
              <button type="button" class="inline-retry" @click="loadAddresses">重试</button>
            </p>
            <div
              v-if="selectedAddress"
              class="pc-address-selected"
            >
              <AddressCardBody :item="selectedAddress" />
              <button type="button" class="text-link" @click="addressListOpen = !addressListOpen">
                {{ addressListOpen ? '收起' : '更换' }}
              </button>
            </div>
            <div v-else-if="addresses.length" class="pc-address-empty">
              <p>请选择收货地址</p>
              <div class="pc-address-actions">
                <el-button type="primary" @click="addressListOpen = true">选择地址</el-button>
                <el-button @click="openAddressForm()">新增地址</el-button>
              </div>
            </div>
            <div v-else class="pc-address-empty">
              <p>暂无收货地址，请先新增</p>
              <el-button type="primary" @click="openAddressForm()">新增地址</el-button>
            </div>
            <div v-if="addressListOpen && addresses.length" class="pc-address-list">
              <div
                v-for="addr in addresses"
                :key="addr.addressId"
                class="pc-address-card"
                :class="{ 'is-selected': addressId === addr.addressId }"
                @click="pickAddress(addr)"
              >
                <span class="addr-radio" :class="{ on: addressId === addr.addressId }" aria-hidden="true" />
                <AddressCardBody :item="addr" />
                <div class="addr-actions" @click.stop>
                  <el-button link type="primary" @click="openAddressForm(addr)">编辑</el-button>
                  <el-button link type="danger" @click="removeAddress(addr.addressId)">删除</el-button>
                </div>
              </div>
            </div>
          </section>

          <section class="pc-panel">
            <header class="panel-head">
              <h2>{{ isCouponRush ? '优惠券信息' : '商品清单' }}</h2>
              <span class="panel-meta">共 {{ totalCount }} 件</span>
            </header>
            <table class="pc-goods-table">
              <thead>
                <tr>
                  <th class="col-goods">商品</th>
                  <th class="col-price">单价</th>
                  <th class="col-qty">数量</th>
                  <th class="col-sub">小计</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(item, index) in items"
                  :key="`${item.productId}-${item.propertyValueIds}-${index}`"
                >
                  <td class="col-goods">
                    <div class="goods-cell">
                      <RouterLink v-if="!isCouponRush" :to="`/product/${item.productId}`" class="goods-cover">
                        <ProductImage :source="item.productCover" width="72" height="72" fit="cover" />
                      </RouterLink>
                      <div v-else class="goods-cover is-coupon">
                        <el-icon :size="28"><Ticket /></el-icon>
                      </div>
                      <div class="goods-meta">
                        <RouterLink v-if="!isCouponRush" :to="`/product/${item.productId}`" class="goods-name">
                          {{ item.productName }}
                        </RouterLink>
                        <p v-else class="goods-name">{{ item.productName }}</p>
                        <p v-if="formatSkuText(item)" class="goods-sku">{{ formatSkuText(item) }}</p>
                      </div>
                    </div>
                  </td>
                  <td class="col-price">¥{{ Number(item.price).toFixed(2) }}</td>
                  <td class="col-qty">×{{ item.buyCount }}</td>
                  <td class="col-sub">¥{{ lineSubtotal(item).toFixed(2) }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section v-if="!isCouponRush" class="pc-panel">
            <h2 class="panel-title">订单备注</h2>
            <el-input
              v-model="remark"
              type="textarea"
              :rows="3"
              maxlength="200"
              show-word-limit
              placeholder="选填：配送、包装等要求"
            />
          </section>

          <section v-if="!isCouponRush" class="pc-panel">
            <header class="panel-head">
              <h2>优惠券</h2>
              <button type="button" class="text-link" @click="openCouponPicker">
                {{ selectedCouponLabel }}
              </button>
            </header>
            <p v-if="couponDiscount > 0" class="panel-tip discount">已抵扣 ¥{{ couponDiscount.toFixed(2) }}</p>
            <p v-if="showMinPayTip" class="panel-tip">使用优惠券后最低需支付 ¥{{ minPayAmountText }}</p>
            <p
              v-else-if="usableCoupons.some((c) => c.usable) && maxAvailableDiscount > 0"
              class="panel-tip highlight"
            >
              您有可用优惠券，最高可抵扣 ¥{{ maxAvailableDiscount.toFixed(2) }}
            </p>
            <p v-if="couponLoadError" class="panel-error">
              {{ couponLoadError }}
              <button type="button" class="inline-retry" @click="loadCoupons">重试</button>
            </p>
          </section>

          <section class="pc-panel">
            <h2 class="panel-title">支付方式</h2>
            <el-radio-group v-model="payMethod" class="pc-pay-methods">
              <label class="pc-pay-option" :class="{ active: payMethod === PAY_METHOD_ALIPAY_PC }">
                <el-radio :label="PAY_METHOD_ALIPAY_PC">支付宝</el-radio>
                <span class="pay-desc">提交后在新窗口打开支付宝扫码支付</span>
              </label>
            </el-radio-group>
          </section>
        </div>

        <aside class="pc-checkout-aside">
          <div class="aside-card">
            <h2>付款详情</h2>
            <div class="amount-row">
              <span>商品件数</span>
              <span>{{ totalCount }} 件</span>
            </div>
            <div class="amount-row">
              <span>商品总价</span>
              <span>¥{{ goodsAmount }}</span>
            </div>
            <div v-if="couponDiscount > 0" class="amount-row">
              <span>优惠券</span>
              <span class="discount">-¥{{ couponDiscount.toFixed(2) }}</span>
            </div>
            <p v-if="showMinPayTip" class="min-pay-tip">已按规则保留最低实付 ¥{{ minPayAmountText }}</p>
            <div class="amount-row total">
              <span>应付总额</span>
              <strong class="price-text">¥{{ payableAmount }}</strong>
            </div>
            <el-button
              type="primary"
              class="btn-submit"
              size="large"
              :loading="submitting"
              :disabled="isCouponRush && payCountdownMs <= 0"
              @click="submit"
            >
              {{ submitButtonText }}
            </el-button>
            <RouterLink to="/cart" class="back-cart">返回购物车</RouterLink>
          </div>
        </aside>
      </div>
    </template>

    <section v-else class="pc-checkout-state">
      <el-empty description="没有待结算的商品">
        <el-button type="primary" @click="router.push('/cart')">返回购物车</el-button>
      </el-empty>
    </section>

    <el-dialog v-model="couponVisible" title="选择优惠券" width="560px">
      <div class="pc-coupon-list">
        <button
          type="button"
          class="pc-coupon-row"
          :class="{ active: !selectedUserCouponId }"
          @click="selectCoupon(null)"
        >
          <div>
            <p class="name">不使用优惠券</p>
            <p class="desc">本单不抵扣</p>
          </div>
        </button>
        <el-skeleton v-if="couponLoading" animated :rows="4" />
        <template v-else>
          <button
            v-for="c in usableCoupons"
            :key="c.userCouponId"
            type="button"
            class="pc-coupon-row"
            :class="{ active: selectedUserCouponId === c.userCouponId, disabled: !c.usable }"
            :disabled="!c.usable"
            @click="selectCoupon(c)"
          >
            <div>
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
            <p class="off">-¥{{ calcCouponDiscount(c).toFixed(2) }}</p>
          </button>
          <el-empty v-if="!usableCoupons.length" description="暂无可用优惠券" />
        </template>
      </div>
      <template #footer>
        <el-button @click="couponVisible = false">取消</el-button>
        <el-button type="primary" :disabled="couponLoading" @click="couponVisible = false">确定</el-button>
      </template>
    </el-dialog>

    <AddressFormPanel
      v-model="addressFormVisible"
      :edit-item="editingAddress"
      @saved="onAddressFormSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { Ticket } from '@element-plus/icons-vue';
import { RouterLink, useRouter } from 'vue-router';
import AddressCardBody from '@/components/business/AddressCardBody.vue';
import AddressFormPanel from '@/components/business/AddressFormPanel.vue';
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
  addresses,
  addressId,
  addressFormVisible,
  editingAddress,
  addressListOpen,
  remark,
  selectedAddress,
  payMethod,
  couponVisible,
  couponLoading,
  usableCoupons,
  couponDiscount,
  payableAmount,
  minPayAmountText,
  showMinPayTip,
  selectedCouponLabel,
  maxAvailableDiscount,
  totalCount,
  goodsAmount,
  selectedUserCouponId,
  formatCouponEnd,
  calcCouponDiscount,
  formatSkuText,
  lineSubtotal,
  init,
  loadAddresses,
  pickAddress,
  openAddressForm,
  onAddressFormSaved,
  removeAddress,
  loadCoupons,
  openCouponPicker,
  selectCoupon,
  submit,
  PAY_METHOD_ALIPAY_PC
} = useCheckoutPage('desktop');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-checkout {
  max-width: 1180px;
  margin: 0 auto;
  padding: 8px 0 40px;
}

.pc-checkout-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  font-size: 14px;

  .step-link {
    color: $color-primary;
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  .step-current {
    font-weight: 600;
    color: $color-text-title;
  }

  .step-muted {
    color: $color-text-muted;
  }

  .step-sep {
    width: 24px;
    height: 1px;
    background: $color-border;
  }
}

.pc-checkout-skeleton {
  padding: 24px;
  background: $color-card;
  border-radius: $radius-card;
}

.pc-checkout-state {
  padding: 48px 24px;
  text-align: center;
  background: $color-card;
  border-radius: $radius-card;
  box-shadow: $shadow-card;

  p {
    margin: 0 0 16px;
    color: $color-text-secondary;
  }
}

.pc-rush-banner {
  margin-bottom: 16px;
  padding: 16px 20px;
  border-radius: $radius-card;
  background: linear-gradient(135deg, rgba($color-primary, 0.1), rgba($color-primary, 0.03));
  border: 1px solid rgba($color-primary, 0.18);

  .rush-title {
    margin: 0 0 6px;
    font-size: 15px;

    strong {
      font-size: 20px;
      color: $color-primary;
      font-variant-numeric: tabular-nums;
    }
  }

  .rush-tip {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
  }

  .rush-expired {
    margin: 0;
    color: $color-error;
  }
}

.pc-checkout-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 20px;
  align-items: start;
}

.pc-panel {
  margin-bottom: 16px;
  padding: 20px 24px;
  background: $color-card;
  border-radius: $radius-card;
  box-shadow: $shadow-card;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;

  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
  }
}

.panel-title {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 600;
  color: $color-text-title;
}

.panel-meta {
  font-size: 13px;
  color: $color-text-muted;
}

.panel-error,
.panel-tip {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.5;
}

.panel-error {
  color: $color-error;
}

.panel-tip {
  color: $color-text-muted;

  &.discount,
  &.highlight {
    color: $color-primary;
  }
}

.text-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: $color-primary;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
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

.pc-address-selected {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid rgba($color-primary, 0.25);
  border-radius: $radius-card;
  background: rgba($color-primary, 0.04);

  :deep(.card-main) {
    flex: 1;
    min-width: 0;
  }
}

.pc-address-empty {
  padding: 20px;
  text-align: center;
  border: 1px dashed $color-border;
  border-radius: $radius-card;

  p {
    margin: 0 0 12px;
    color: $color-text-muted;
  }
}

.pc-address-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}

.pc-address-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.pc-address-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid $color-border;
  border-radius: $radius-card;
  cursor: pointer;
  transition: border-color $transition-fast, background $transition-fast;

  &:hover {
    border-color: rgba($color-primary, 0.35);
  }

  &.is-selected {
    border-color: rgba($color-primary, 0.55);
    background: rgba($color-primary, 0.04);
  }

  :deep(.card-main) {
    flex: 1;
    min-width: 0;
  }
}

.addr-radio {
  width: 16px;
  height: 16px;
  margin-top: 4px;
  border: 2px solid $color-border;
  border-radius: 50%;
  flex-shrink: 0;

  &.on {
    border-color: $color-primary;
    box-shadow: inset 0 0 0 3px $color-card, inset 0 0 0 8px $color-primary;
  }
}

.addr-actions {
  flex-shrink: 0;
}

.pc-goods-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;

  th {
    padding: 10px 12px;
    text-align: left;
    font-weight: 500;
    color: $color-text-muted;
    border-bottom: 1px solid $color-border;
  }

  td {
    padding: 14px 12px;
    vertical-align: middle;
    border-bottom: 1px solid $color-border-light;
  }

  tr:last-child td {
    border-bottom: none;
  }

  .col-price,
  .col-qty,
  .col-sub {
    width: 100px;
    white-space: nowrap;
  }

  .col-sub {
    font-weight: 600;
    color: $color-text-title;
  }
}

.goods-cell {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.goods-cover {
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

.goods-meta {
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

  &:hover {
    color: $color-primary;
  }
}

.goods-sku {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
}

.pc-pay-methods {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pc-pay-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid $color-border;
  border-radius: $radius-card;
  cursor: pointer;

  &.active {
    border-color: rgba($color-primary, 0.5);
    background: rgba($color-primary, 0.04);
  }

  .pay-desc {
    margin-left: 24px;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.pc-checkout-aside {
  position: sticky;
  top: 88px;
}

.aside-card {
  padding: 20px;
  background: $color-card;
  border-radius: $radius-card;
  box-shadow: $shadow-card;

  h2 {
    margin: 0 0 14px;
    font-size: 16px;
    font-weight: 600;
  }
}

.amount-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  font-size: 14px;
  color: $color-text-body;

  .discount {
    color: $color-price;
  }

  &.total {
    margin-top: 8px;
    padding-top: 14px;
    border-top: 1px dashed $color-border;
    font-size: 15px;

    .price-text {
      font-size: 24px;
      color: $color-price;
    }
  }
}

.min-pay-tip {
  margin: 0 0 8px;
  font-size: 12px;
  color: $color-text-muted;
}

.btn-submit {
  width: 100%;
  margin-top: 16px;
  height: 44px;
  font-weight: 600;
}

.back-cart {
  display: block;
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: $color-text-muted;
  text-decoration: none;

  &:hover {
    color: $color-primary;
  }
}

.pc-coupon-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 420px;
  overflow-y: auto;
}

.pc-coupon-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 12px 14px;
  border: 1px solid $color-border;
  border-radius: $radius-card;
  background: $color-card;
  text-align: left;
  cursor: pointer;

  &.active {
    border-color: rgba($color-primary, 0.55);
    background: rgba($color-primary, 0.05);
  }

  &.disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .name {
    margin: 0 0 4px;
    font-size: 14px;
    font-weight: 600;
  }

  .desc {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
  }

  .off {
    margin: 0;
    font-size: 16px;
    font-weight: 700;
    color: $color-price;
    flex-shrink: 0;
  }
}
</style>
