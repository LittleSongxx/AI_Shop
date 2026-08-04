<template>
  <div class="m-simple">
    <div class="m-toolbar">
      <button type="button" class="op-btn primary" @click="showEdit()">新增优惠券</button>
    </div>

    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.couponNameFuzzy" class="search-input" placeholder="优惠券名称" @keyup.enter="reload" />
      <select v-model="searchForm.status" class="search-select" @change="reload">
        <option value="">全部</option>
        <option :value="1">进行中</option>
        <option :value="0">已停用</option>
        <option :value="2">已过期</option>
        <option :value="3">已发完</option>
      </select>
    </div>

    <p class="m-note">支持新增/编辑、启用停用与秒杀库存维护。</p>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.couponId" class="glass-card coupon-card">
        <div class="coupon-left">
          <span class="coupon-amount">
            <template v-if="row.couponType == 2">{{ (row.discountRate * 10).toFixed(1) }}折</template>
            <template v-else>¥{{ row.discountAmount }}</template>
          </span>
          <span class="coupon-type">{{ typeText(row.couponType) }}</span>
        </div>
        <div class="coupon-mid">
          <span class="coupon-name">{{ row.couponName }}</span>
          <span class="coupon-stock">库存 {{ row.remainCount || 0 }} / {{ row.totalCount == 0 ? '不限' : row.totalCount }}</span>
          <span class="coupon-valid">{{ row.validStartTime || '--' }} ~ {{ row.validEndTime || '--' }}</span>
          <div class="coupon-tags">
            <span class="m-tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
            <span v-if="isRush(row)" class="m-tag danger">秒杀</span>
          </div>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无优惠券</p>

    <div v-if="list.length" class="m-list ops-list">
      <div v-for="row in list" :key="'op-' + row.couponId" class="glass-card coupon-ops">
        <span class="ops-name">{{ row.couponName }}</span>
        <button type="button" class="op-btn" @click="showEdit(row.couponId)">编辑</button>
        <button type="button" class="op-btn" @click="toggleStatus(row)">{{ row.status == 0 ? '启用' : '停用' }}</button>
        <template v-if="isRush(row)">
          <button type="button" class="op-btn" @click="warmupOne(row)">预热</button>
          <button type="button" class="op-btn" @click="reconcileOne(row)">对账</button>
        </template>
      </div>
    </div>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="currentCouponId ? '修改优惠券' : '新增优惠券'"
      width="92%"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
        <el-form-item label="优惠券名称" prop="couponName">
          <el-input v-model="form.couponName" placeholder="请输入优惠券名称" />
        </el-form-item>
        <el-form-item label="优惠券类型" prop="couponType">
          <el-radio-group v-model="form.couponType" @change="onCouponTypeChange">
            <el-radio :label="1">满减券</el-radio>
            <el-radio :label="2">折扣券</el-radio>
            <el-radio :label="3">无门槛券</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.couponType !== 3" label="使用门槛金额" prop="thresholdAmount">
          <el-input-number v-model="form.thresholdAmount" :min="0" :precision="2" :step="1" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="form.couponType !== 2" label="优惠金额" prop="discountAmount">
          <el-input-number v-model="form.discountAmount" :min="0" :precision="2" :step="1" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="form.couponType === 2" label="折扣率" prop="discountRate">
          <el-input-number v-model="form.discountRate" :min="0" :max="1" :precision="2" :step="0.05" style="width: 100%" />
          <div class="m-tip">如 0.85 表示 85 折</div>
        </el-form-item>
        <el-form-item label="发放总量" prop="totalCount">
          <el-input-number v-model="form.totalCount" :min="0" :step="1" style="width: 100%" />
          <div class="m-tip">0 表示不限量</div>
        </el-form-item>
        <el-form-item label="有效期开始" prop="validStartTime">
          <el-date-picker
            v-model="form.validStartTime"
            type="datetime"
            placeholder="开始时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="有效期结束" prop="validEndTime">
          <el-date-picker
            v-model="form.validEndTime"
            type="datetime"
            placeholder="结束时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="上架秒杀" prop="rushingstatus">
          <el-switch v-model="form.rushingstatus" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <template v-if="form.rushingstatus === 1">
          <el-form-item label="秒杀开始时间" prop="rushingStartTime">
            <el-date-picker
              v-model="form.rushingStartTime"
              type="datetime"
              placeholder="秒杀开始"
              format="YYYY-MM-DD HH:mm:ss"
              value-format="YYYY-MM-DD HH:mm:ss"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="秒杀结束时间" prop="rushingEndTime">
            <el-date-picker
              v-model="form.rushingEndTime"
              type="datetime"
              placeholder="秒杀结束"
              format="YYYY-MM-DD HH:mm:ss"
              value-format="YYYY-MM-DD HH:mm:ss"
              style="width: 100%"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <button type="button" class="op-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="op-btn primary" @click="handleSave">保存</button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ couponNameFuzzy: '', status: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
const dialogVisible = ref(false)
const formRef = ref()
const currentCouponId = ref(null)
let observer = null

const form = reactive({
  couponName: '',
  couponType: 1,
  thresholdAmount: 0,
  discountAmount: 0,
  discountRate: 0.9,
  totalCount: 0,
  validStartTime: '',
  validEndTime: '',
  rushingstatus: 0,
  rushingStartTime: '',
  rushingEndTime: '',
})

const rules = computed(() => {
  const rule = {
    couponName: [{ required: true, message: '请输入优惠券名称', trigger: 'blur' }],
    couponType: [{ required: true, message: '请选择优惠券类型', trigger: 'change' }],
    discountAmount: [{ required: true, message: '请输入优惠金额', trigger: 'blur' }],
    discountRate: [{ required: true, message: '请输入折扣率', trigger: 'blur' }],
    totalCount: [{ required: true, message: '请输入发放总量', trigger: 'blur' }],
    validStartTime: [{ required: true, message: '请选择有效期开始时间', trigger: 'change' }],
    validEndTime: [{ required: true, message: '请选择有效期结束时间', trigger: 'change' }],
  }
  if (form.couponType !== 3) {
    rule.thresholdAmount = [{ required: true, message: '请输入使用门槛金额', trigger: 'blur' }]
  }
  if (form.rushingstatus === 1) {
    rule.rushingStartTime = [{ required: true, message: '请选择秒杀开始时间', trigger: 'change' }]
    rule.rushingEndTime = [{ required: true, message: '请选择秒杀结束时间', trigger: 'change' }]
  }
  return rule
})

const typeText = (t) => (t == 1 ? '满减券' : t == 2 ? '折扣券' : '无门槛')
const statusText = (s) => (s == 0 ? '已停用' : s == 1 ? '进行中' : s == 2 ? '已过期' : '已发完')
const statusClass = (s) => (s == 1 ? 'green' : s == 0 ? 'muted' : 'gold')
const isRush = (row) => row.rushingstatus == 1 || row.rushingStatus == 1

const loadList = async (reset = false) => {
  if (loading.value) return
  if (reset) {
    pageNo.value = 0
    pageTotal.value = 1
    finished.value = false
    list.value = []
  }
  if (finished.value) return
  loading.value = true
  try {
    const next = pageNo.value + 1
    const params = { pageNo: next, pageSize: 10 }
    if (searchForm.couponNameFuzzy) params.couponNameFuzzy = searchForm.couponNameFuzzy
    if (searchForm.status !== '') params.status = searchForm.status
    const result = await proxy.Request({ url: proxy.Api.loadDiscountCoupon, params, showLoading: false })
    if (!result) return
    const data = result.data || {}
    const chunk = data.list || []
    list.value = next === 1 ? chunk : list.value.concat(chunk)
    pageNo.value = Number(data.pageNo) || next
    pageTotal.value = Number(data.pageTotal) || pageNo.value
    finished.value = pageNo.value >= pageTotal.value
  } finally {
    loading.value = false
  }
}

const reload = () => loadList(true)

const toggleStatus = (row) => {
  proxy.ConfirmSensitive({
    message: `确定要${row.status == 0 ? '启用' : '停用'}「${row.couponName}」吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      const result = await proxy.Request({
        url: proxy.Api.updateDiscountCouponStatus,
        sensitiveConfirmPwd,
        params: { couponId: row.couponId, status: row.status == 0 ? 1 : 0 }
      })
      if (!result) return
      proxy.Message.success('操作成功')
      reload()
    }
  })
}

const warmupOne = (row) => {
  proxy.Confirm({
    message: `预热「${row.couponName}」的秒杀 Redis 库存？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.warmupRushStock, params: { couponId: row.couponId }, showLoading: true })
      if (!result) return
      proxy.Message.success('预热成功')
    }
  })
}

const reconcileOne = (row) => {
  proxy.Confirm({
    message: `对「${row.couponName}」执行 Redis/DB 对账？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.reconcileRushStock, params: { couponId: row.couponId }, showLoading: true })
      if (!result) return
      proxy.Message.success('对账完成')
    }
  })
}

const showEdit = async (couponId) => {
  currentCouponId.value = couponId || null
  dialogVisible.value = true
  Object.assign(form, {
    couponName: '',
    couponType: 1,
    thresholdAmount: 0,
    discountAmount: 0,
    discountRate: 0.9,
    totalCount: 0,
    validStartTime: '',
    validEndTime: '',
    rushingstatus: 0,
    rushingStartTime: '',
    rushingEndTime: '',
  })
  if (couponId) {
    const result = await proxy.Request({
      url: proxy.Api.getDiscountCouponInfo,
      params: { couponId },
      showLoading: true,
    })
    if (!result) {
      dialogVisible.value = false
      return
    }
    Object.assign(form, result.data)
  }
}

const onCouponTypeChange = () => {
  if (form.couponType === 2) form.discountAmount = 0
  else form.discountRate = 0.9
  if (form.couponType === 3) form.thresholdAmount = 0
}

const handleSave = async () => {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    const params = { ...form }
    if (currentCouponId.value) params.couponId = currentCouponId.value
    const result = await proxy.Request({
      url: proxy.Api.saveDiscountCoupon,
      params,
      showLoading: true,
    })
    if (!result) return
    proxy.Message.success('保存成功')
    dialogVisible.value = false
    reload()
  })
}

onMounted(() => {
  loadList(true)
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadList()
    },
    { rootMargin: '0px 0px 300px 0px' }
  )
  if (sentinel.value) observer.observe(sentinel.value)
})

onUnmounted(() => {
  observer && observer.disconnect()
  observer = null
})
</script>

<style lang="scss" scoped>
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  border-radius: 8px;

  .search-input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    font-size: 14px;
    color: var(--m-ink);
    outline: none;

    &::placeholder {
      color: var(--m-ink-3);
    }
  }

  .search-select {
    flex-shrink: 0;
    border: none;
    background: rgba(255, 255, 255, 0.5);
    border-radius: 8px;
    padding: 4px 6px;
    font-size: 13px;
    color: var(--m-ink-2);
    outline: none;
  }
}

.m-note {
  margin: 0;
  font-size: 12px;
  color: var(--m-ink-3);
  line-height: 1.5;
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.coupon-card {
  display: flex;
  gap: 12px;
  padding: 12px 14px;

  .coupon-left {
    flex-shrink: 0;
    width: 78px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border-right: 1px dashed rgba(120, 120, 128, 0.3);

    .coupon-amount {
      font-size: 19px;
      font-weight: 700;
      color: var(--m-gold);
    }

    .coupon-type {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .coupon-mid {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;

    .coupon-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--m-ink);
    }

    .coupon-stock,
    .coupon-valid {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .coupon-tags {
    display: flex;
    gap: 6px;
    margin-top: 2px;
  }
}

.ops-list .coupon-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;

  .ops-name {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    color: var(--m-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .op-btn {
    flex-shrink: 0;
    height: 30px;
    padding: 0 12px;
    border: 1px solid rgba(120, 120, 128, 0.24);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.5);
    color: var(--m-ink-2);
    font-size: 12px;
    cursor: pointer;
  }
}

.m-tag {
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 11px;

  &.green {
    background: rgba(52, 199, 89, 0.16);
    color: #1c8c3c;
  }

  &.muted {
    background: rgba(120, 120, 128, 0.16);
    color: var(--m-ink-2);
  }

  &.gold {
    background: var(--m-gold-soft);
    color: #1d4ed8;
  }

  &.danger {
    background: rgba(255, 59, 48, 0.14);
    color: var(--m-danger);
  }
}

.m-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  font-size: 12px;
  color: var(--m-ink-3);
}

.m-empty-tip {
  margin: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--m-ink-3);
}
</style>
