<template>
  <div class="m-simple">
    <div class="glass-card m-form">
      <h3 class="m-form-title">会员升级礼券</h3>

      <div class="m-field">
        <label class="m-label">银卡升级礼券</label>
        <select v-model="form.level2CouponId" class="m-select">
          <option value="">选填：银卡会员领取的优惠券</option>
          <option v-for="c in couponOptions" :key="c.couponId" :value="c.couponId">
            {{ couponLabel(c) }}
          </option>
        </select>
        <p v-if="silverCoupon" class="m-tip">
          剩余 {{ silverCoupon.remainCount ?? 0 }} /
          {{ silverCoupon.totalCount == 0 ? '不限' : silverCoupon.totalCount }}
          · {{ silverCoupon.validStartTime || '—' }} ~ {{ silverCoupon.validEndTime || '—' }}
        </p>
        <span class="m-tip">用户成长值达 1000（银卡）且首次领取升级礼时发放</span>
      </div>

      <div class="m-field">
        <label class="m-label">金卡升级礼券</label>
        <select v-model="form.level3CouponId" class="m-select">
          <option value="">选填：金卡会员领取的优惠券</option>
          <option v-for="c in couponOptions" :key="'g-' + c.couponId" :value="c.couponId">
            {{ couponLabel(c) }}
          </option>
        </select>
        <p v-if="goldCoupon" class="m-tip">
          剩余 {{ goldCoupon.remainCount ?? 0 }} /
          {{ goldCoupon.totalCount == 0 ? '不限' : goldCoupon.totalCount }}
          · {{ goldCoupon.validStartTime || '—' }} ~ {{ goldCoupon.validEndTime || '—' }}
        </p>
        <span class="m-tip">用户成长值达 5000（金卡）且首次领取升级礼时发放</span>
      </div>

      <div class="m-form-ops">
        <button type="button" class="op-btn primary" :disabled="saving" @click="save">保存到 Redis</button>
        <button type="button" class="op-btn" @click="load">重新加载</button>
      </div>

      <p class="m-note" style="margin-top: 12px">
        配置保存在 Redis，保存后立即对用户「会员中心」领取升级礼生效。留空则仅发放成长值与站内通知，不发券。
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, reactive, ref } from 'vue'

const { proxy } = getCurrentInstance()
const saving = ref(false)
const couponOptions = ref([])

const form = reactive({
  level2CouponId: '',
  level3CouponId: '',
})

const silverCoupon = computed(() => couponOptions.value.find((c) => c.couponId === form.level2CouponId))
const goldCoupon = computed(() => couponOptions.value.find((c) => c.couponId === form.level3CouponId))

const couponLabel = (c) => {
  const type = c.couponType == 1 ? '满减' : c.couponType == 2 ? '折扣' : c.couponType == 3 ? '无门槛' : '券'
  return `${c.couponName}（${c.couponId}，${type}）`
}

const loadCoupons = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadDiscountCoupon,
    params: { pageNo: 1, pageSize: 200, status: 1 },
  })
  if (!result) return
  couponOptions.value = result.data?.list || []
}

const load = async () => {
  const result = await proxy.Request({
    url: proxy.Api.memberLevelRewardGetConfig,
    showLoading: true,
  })
  if (!result) return
  const data = result.data || {}
  form.level2CouponId = data.level2CouponId || ''
  form.level3CouponId = data.level3CouponId || ''
}

const save = async () => {
  saving.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.memberLevelRewardSaveConfig,
      params: {
        level2CouponId: form.level2CouponId || '',
        level3CouponId: form.level3CouponId || '',
      },
      showLoading: true,
    })
    if (!result) return
    proxy.Message.success('已保存到 Redis')
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await loadCoupons()
  await load()
})
</script>
