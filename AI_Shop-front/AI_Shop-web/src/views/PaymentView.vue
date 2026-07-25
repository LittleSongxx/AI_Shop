<template>
  <div class="pay-page">
    <section class="pay-card card-flat">
      <h2 class="pay-title">订单支付</h2>
      <p class="pay-order-no">支付单号：{{ payOrderId() }}</p>

      <div v-if="orderInfo" class="pay-summary">
        <div class="summary-row">
          <span>订单状态</span>
          <span>{{ orderStatusLabel(orderInfo.orderStatus) }}</span>
        </div>
        <div v-if="orderInfo.subject" class="summary-row">
          <span>订单说明</span>
          <span class="subject">{{ orderInfo.subject }}</span>
        </div>
        <div class="summary-row highlight">
          <span>应付金额</span>
          <strong class="price-text">¥{{ formatMoney(payAmount) }}</strong>
        </div>
      </div>

      <div v-if="paySuccess" class="pay-result success">
        <p class="result-title">支付成功</p>
        <p class="result-desc">订单已支付，可在「我的订单」查看</p>
        <div class="pay-actions">
          <el-button type="primary" round @click="goOrders">查看订单</el-button>
          <el-button round @click="goHome">继续购物</el-button>
        </div>
      </div>

      <div v-else-if="payLaunched" class="pay-result pending">
        <p class="result-title">请在支付宝完成支付</p>
        <p class="result-desc">若未自动跳转，请点击下方按钮重新唤起支付宝。</p>
        <el-button type="primary" plain round :loading="reopening" @click="reopenPayPage">
          重新跳转支付宝
        </el-button>
        <div class="pay-check">
          <el-button type="primary" round :loading="checking" @click="checkPay">我已支付</el-button>
        </div>
        <div class="pay-actions secondary">
          <el-button round @click="goOrders">稍后支付</el-button>
        </div>
      </div>

      <div v-else-if="loadError" class="pay-result error">
        <p class="result-desc">{{ loadError }}</p>
        <el-button type="primary" round :loading="launching" @click="startPay">重试</el-button>
      </div>

      <div v-else-if="isProcessing" class="pay-result pending">
        <p class="result-title">支付处理中</p>
        <p class="result-desc">正在确认支付结果，请稍候；也可点击下方按钮手动查询。</p>
        <div class="pay-check">
          <el-button type="primary" round :loading="checking" @click="checkPay">查询支付结果</el-button>
        </div>
      </div>

      <div v-else class="pay-loading">
        <p>正在跳转支付宝…</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
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
} = usePaymentPage('mobile');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pay-page {
  min-height: calc(100vh - 120px);
  display: flex;
  justify-content: center;
  padding: 8px 0 24px;
}

.pay-card {
  width: 100%;
  max-width: 420px;
  padding: 20px 16px;
  text-align: center;
}

.pay-title {
  margin: 0 0 8px;
  font-size: 20px;
  font-weight: 700;
  color: $color-text-title;
}

.pay-order-no {
  margin: 0 0 16px;
  font-size: 12px;
  color: $color-text-muted;
  word-break: break-all;
}

.pay-summary {
  margin-bottom: 20px;
  padding: 12px;
  border-radius: $radius-card;
  background: #fafafa;
  text-align: left;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 6px 0;
  font-size: 14px;
  color: $color-text-body;

  .subject {
    text-align: right;
    flex: 1;
    word-break: break-word;
  }

  &.highlight .price-text {
    font-size: 22px;
    color: $color-price;
  }
}

.pay-result {
  padding: 8px 0;
}

.result-title {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
  color: $color-text-title;
}

.result-desc {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.5;
  color: $color-text-muted;
}

.pay-check {
  margin: 16px 0 12px;
}

.pay-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;

  &.secondary {
    margin-top: 8px;
  }
}

.pay-loading {
  padding: 24px 0;
  font-size: 14px;
  color: $color-text-muted;
}
</style>
