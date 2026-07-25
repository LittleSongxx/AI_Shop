<template>
  <div class="order-item">
    <div class="order-title">
      <div class="order-base">
        <div class="order-time">{{ data.orderTime }}</div>
        <div class="order-id">订单号: {{ data.orderId }}</div>
        <div class="buyer">买家信息:{{ data.userId }} / {{ data.nickName }}</div>
      </div>
      <div class="order-status">{{ data.orderStatusName }}</div>
    </div>
    <div class="sub-item-list">
      <div class="order-sub-item" v-for="(sub, index) in data.orderItemList">
        <CouponOrderCover v-if="isCouponItem(sub)" :width="80" />
        <Cover v-else :source="sub.cover" :width="80"></Cover>
        <div class="product-name-panel">
          <div class="product-name">{{ sub.productName }}</div>
          <div class="property">{{ sub.propertyInfo }}</div>
          <div class="remark">买家备注:{{ sub.remark || '暂无' }}</div>
        </div>
        <div class="amount-info">
          <Price :price="sub.itemAmount"></Price>
          <div class="buy-count">x{{ sub.buyCount }}</div>
        </div>
        <div class="total-amount">
          <template v-if="index == 0">
            <div class="amount-detail">
              <div v-if="hasCouponDiscount" class="amount-line muted">
                <span>商品总价</span>
                <Price :price="data.originalAmount"></Price>
              </div>
              <div v-if="hasCouponDiscount" class="amount-line discount">
                <span>优惠券{{ couponLabel ? `：${couponLabel}` : '' }}</span>
                <span>-¥{{ formatDiscount(data.couponDiscountAmount) }}</span>
              </div>
              <div class="amount-line pay">
                <span class="total-amount-tips">实付款</span>
                <Price :price="data.amount"></Price>
              </div>
            </div>
          </template>
        </div>
        <div class="order-op-panel">
          <template v-if="index == 0">
            <el-button type="primary" v-if="data.orderStatus == 1" class="btn" @click="delivery">确认发货</el-button>
            <el-button type="primary" v-if="data.commentStatus != 0" @click="comment">回复买家</el-button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { isCouponOrder, isCouponOrderItem } from '@/utils/order.js'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const props = defineProps({
  data: {
    type: Object,
    default: {},
  },
})

const isCouponItem = (item) => isCouponOrder(props.data) || isCouponOrderItem(item)

const hasCouponDiscount = computed(() => Number(props.data?.couponDiscountAmount ?? 0) > 0)

const couponLabel = computed(() => {
  const name = props.data?.couponName
  if (!name) return ''
  const typeMap = { 1: '满减券', 2: '折扣券', 3: '无门槛券' }
  const type = typeMap[props.data?.couponType]
  return type ? `${name}（${type}）` : String(name)
})

const formatDiscount = (val) => Number(val ?? 0).toFixed(2)

const totalAmount = computed(() => {
  return props.data.orderItemList.reduce((total, item) => {
    return total + item.amount
  }, 0)
})

const emit = defineEmits(['delivery', 'comment'])
const delivery = () => {
  emit('delivery', props.data)
}

const commentRef = ref()
const comment = () => {
  emit('comment', props.data.orderId)
}
</script>

<style lang="scss" scoped>
.order-item {
  .order-title {
    background: #ebebeb;
    padding: 10px;
    display: flex;
    border-radius: 5px;
    color: var(--text);

    .order-base {
      display: flex;
      flex: 1;
      width: 0;

      .order-id {
        margin-left: 20px;
      }

      .buyer {
        margin-left: 20px;
      }
    }

    .order-status {
      color: var(--red);
    }
  }

  .sub-item-list {
    .order-sub-item {
      display: flex;
      margin: 10px 0px 0px;
      align-items: flex-start;

      .product-name-panel {
        flex: 1;
        width: 0;
        margin: 0px 10px;
        color: var(--text);

        .product-name {
          cursor: pointer;

          &:hover {
            color: var(--red);
          }
        }

        .property {
          margin-top: 5px;
          font-size: 12px;
          color: var(--text2);
        }

        .remark {
          margin-top: 5px;
          font-size: 12px;
          color: #000;
        }
      }

      .amount-info {
        width: 100px;
        display: flex;
        flex-direction: column;
        align-items: flex-end;

        .buy-count {
          font-size: 12px;
          color: var(--text2);
        }
      }

      .total-amount {
        width: 220px;
        display: flex;
        align-items: flex-end;
        justify-content: flex-end;
        font-size: 12px;

        .amount-detail {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 4px;
          width: 100%;
        }

        .amount-line {
          display: flex;
          align-items: baseline;
          justify-content: flex-end;
          gap: 6px;
          width: 100%;

          &.muted {
            color: var(--text2);
          }

          &.discount {
            color: var(--red);
          }

          &.pay {
            margin-top: 2px;
          }
        }

        .total-amount-tips {
          margin-right: 3px;
        }
      }

      .order-op-panel {
        margin-left: 10px;
        width: 110px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;

        .btn {
          width: 90px;
          margin-bottom: 10px;
        }

        .btn-link {
          cursor: pointer;
        }
      }
    }
  }
}
</style>
