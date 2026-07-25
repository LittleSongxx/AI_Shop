<template>
  <div class="pc-payment ignore">
    <nav class="pc-payment-steps" aria-label="支付流程">
      <RouterLink to="/cart" class="step-link">购物车</RouterLink>
      <span class="step-sep" aria-hidden="true" />
      <RouterLink to="/checkout" class="step-link">确认订单</RouterLink>
      <span class="step-sep" aria-hidden="true" />
      <span class="step-current">支付</span>
    </nav>

    <div class="pc-payment-layout">
      <section class="pc-payment-main">
        <header class="main-head">
          <h1>订单支付</h1>
          <p class="order-no">支付单号：{{ payOrderId() }}</p>
        </header>

        <div v-if="orderInfo" class="order-summary">
          <div class="summary-row">
            <span class="label">订单状态</span>
            <span>{{ orderStatusLabel(orderInfo.orderStatus) }}</span>
          </div>
          <div v-if="orderInfo.subject" class="summary-row">
            <span class="label">订单说明</span>
            <span class="value">{{ orderInfo.subject }}</span>
          </div>
          <div class="summary-row highlight">
            <span class="label">应付金额</span>
            <strong class="price-text">¥{{ formatMoney(payAmount) }}</strong>
          </div>
        </div>

        <div v-if="paySuccess" class="result-card success">
          <div class="result-icon" aria-hidden="true">✓</div>
          <h2>支付成功</h2>
          <p>订单已支付，可在「我的订单」查看详情与物流信息。</p>
          <div class="result-actions">
            <el-button type="primary" size="large" @click="goOrders">查看订单</el-button>
            <el-button size="large" @click="goHome">继续购物</el-button>
          </div>
        </div>

        <div v-else-if="payLaunched" class="result-card pending">
          <h2>请在支付宝页面完成支付</h2>
          <p>
            支付页已在新窗口打开。若未弹出，请允许浏览器弹窗后点击下方按钮重新打开。
            完成支付后请点击「我已支付」同步订单状态。
          </p>
          <div class="pending-actions">
            <el-button type="primary" plain :loading="reopening" @click="reopenPayPage">
              重新打开支付页
            </el-button>
            <el-button type="primary" :loading="checking" @click="checkPay">我已支付</el-button>
          </div>
          <div class="secondary-actions">
            <el-button link @click="goOrders">稍后支付，查看订单</el-button>
          </div>
        </div>

        <div v-else-if="loadError" class="result-card error">
          <h2>支付发起失败</h2>
          <p>{{ loadError }}</p>
          <el-button type="primary" :loading="launching" @click="startPay">重试</el-button>
        </div>

        <div v-else-if="isProcessing" class="result-card pending">
          <h2>支付处理中</h2>
          <p>正在确认支付结果，请稍候；也可点击下方按钮手动查询。</p>
          <el-button type="primary" :loading="checking" @click="checkPay">查询支付结果</el-button>
        </div>

        <div v-else class="result-card loading">
          <el-skeleton animated :rows="3" />
          <p class="loading-tip">正在跳转支付宝…</p>
        </div>
      </section>

      <aside class="pc-payment-aside">
        <div class="aside-card">
          <h2>支付说明</h2>
          <ul class="tips-list">
            <li>推荐使用支付宝扫码或登录账户完成付款。</li>
            <li>支付完成后系统会自动同步状态，通常几秒内生效。</li>
            <li>若长时间未更新，请点击「我已支付」手动查询。</li>
            <li>遇到问题可联系智能客服或查看订单详情。</li>
          </ul>
          <div class="secure-note">
            <span class="secure-badge">安全支付</span>
            <span>由支付宝提供担保交易</span>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router';
import { usePaymentPage } from '@/composables/usePaymentPage';

const {
  orderInfo,
  payAmount,
  payLaunched,
  paySuccess,
  launching,
  reopening,
  checking,
  loadError,
  isProcessing,
  payOrderId,
  formatMoney,
  orderStatusLabel,
  startPay,
  reopenPayPage,
  goOrders,
  goHome,
  checkPay
} = usePaymentPage('desktop');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-payment {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 0 48px;
}

.pc-payment-steps {
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

  .step-sep {
    width: 24px;
    height: 1px;
    background: $color-border;
  }
}

.pc-payment-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 20px;
  align-items: start;
}

.pc-payment-main {
  padding: 28px 32px;
  background: $color-card;
  border-radius: $radius-card;
  box-shadow: $shadow-card;
}

.main-head {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid $color-border-light;

  h1 {
    margin: 0 0 8px;
    font-size: 22px;
    font-weight: 700;
    color: $color-text-title;
  }

  .order-no {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
    word-break: break-all;
  }
}

.order-summary {
  margin-bottom: 24px;
  padding: 16px 18px;
  border-radius: $radius-card;
  background: $color-bg-subtle;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 8px 0;
  font-size: 14px;

  .label {
    color: $color-text-muted;
    flex-shrink: 0;
  }

  .value {
    text-align: right;
    word-break: break-word;
  }

  &.highlight .price-text {
    font-size: 28px;
    color: $color-price;
  }
}

.result-card {
  padding: 8px 0;

  h2 {
    margin: 0 0 10px;
    font-size: 18px;
    font-weight: 600;
    color: $color-text-title;
  }

  p {
    margin: 0 0 20px;
    font-size: 14px;
    line-height: 1.6;
    color: $color-text-secondary;
    max-width: 520px;
  }

  &.success {
    text-align: left;

    .result-icon {
      width: 48px;
      height: 48px;
      margin-bottom: 12px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      font-size: 24px;
      font-weight: 700;
      color: #fff;
      background: $color-success;
    }
  }
}

.result-actions,
.pending-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.secondary-actions {
  margin-top: 12px;
}

.loading-tip {
  margin-top: 12px;
  font-size: 14px;
  color: $color-text-muted;
}

.pc-payment-aside {
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
    font-size: 15px;
    font-weight: 600;
  }
}

.tips-list {
  margin: 0 0 16px;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: $color-text-secondary;

  li + li {
    margin-top: 6px;
  }
}

.secure-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid $color-border-light;
  font-size: 12px;
  color: $color-text-muted;
}

.secure-badge {
  padding: 2px 8px;
  border-radius: $radius-pill;
  background: rgba($color-success, 0.12);
  color: $color-success;
  font-weight: 600;
}
</style>
