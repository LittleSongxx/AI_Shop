<template>
  <div class="search-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="优惠券名称">
            <el-input clearable placeholder="输入优惠券名称" v-model="searchForm.couponNameFuzzy"></el-input>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="类型" prop="">
            <el-select clearable placeholder="请选择类型" v-model="searchForm.couponType">
              <el-option :value="1" label="满减券"></el-option>
              <el-option :value="2" label="折扣券"></el-option>
              <el-option :value="3" label="无门槛券"></el-option>
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="状态" prop="">
            <el-select clearable placeholder="请选择状态" v-model="searchForm.status">
              <el-option :value="0" label="已停用"></el-option>
              <el-option :value="1" label="进行中"></el-option>
              <el-option :value="2" label="已过期"></el-option>
              <el-option :value="3" label="已发完"></el-option>
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="13" class="toolbar-actions">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
          <el-button type="success" @click="showEdit()">新增优惠券</el-button>
          <el-button type="warning" plain @click="warmupAllRush">秒杀库存预热(全部)</el-button>
          <el-button type="warning" plain @click="reconcileAllRush">秒杀库存对账(全部)</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <div class="table-panel">
      <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
        <template #slotType="{ index, row }">
          <el-tag v-if="row.couponType == 1" effect="dark" type="primary">满减券</el-tag>
          <el-tag v-if="row.couponType == 2" effect="dark" type="warning">折扣券</el-tag>
          <el-tag v-if="row.couponType == 3" effect="dark" type="success">无门槛券</el-tag>
        </template>

        <template #slotAmount="{ index, row }">
          <div v-if="row.couponType == 2">
            {{ (row.discountRate * 10).toFixed(1) }}折
          </div>
          <div v-else>
            ¥{{ row.discountAmount }}
          </div>
        </template>

        <template #slotStock="{ index, row }">
          {{ row.remainCount || 0 }} / {{ row.totalCount == 0 ? '不限' : row.totalCount }}
        </template>

        <template #slotValidTime="{ index, row }">
          {{ row.validStartTime || '--' }} ~ {{ row.validEndTime || '--' }}
        </template>

        <template #slotRush="{ row }">
          <el-tag v-if="row.rushingstatus == 1 || row.rushingStatus == 1" type="danger" effect="plain">秒杀</el-tag>
          <span v-else class="text-muted">—</span>
        </template>

        <template #slotStatus="{ index, row }">
          <el-tag v-if="row.status == 0" effect="dark" type="info">已停用</el-tag>
          <el-tag v-if="row.status == 1" effect="dark" type="success">进行中</el-tag>
          <el-tag v-if="row.status == 2" effect="dark" type="danger">已过期</el-tag>
          <el-tag v-if="row.status == 3" effect="dark" type="warning">已发完</el-tag>
        </template>

        <template #slotOp="{ index, row }">
          <div class="list-op-panel">
            <OpBtn icon="icon-edit" tips="修改" @click="showEdit(row.couponId)"></OpBtn>
            <OpBtn :icon="row.status == 0 ? 'icon-shangjia' : 'icon-xiajia'"
              :tips="row.status == 0 ? '启用' : '停用'" @click="toggleStatus(row)">
            </OpBtn>
            <template v-if="row.rushingstatus == 1 || row.rushingStatus == 1">
              <OpBtn icon="icon-stock" tips="预热Redis库存" @click="warmupOne(row)"></OpBtn>
              <OpBtn icon="icon-view" tips="Redis对账" @click="reconcileOne(row)"></OpBtn>
            </template>
          </div>
        </template>
      </Table>
    </div>
  </el-card>

  <el-dialog
    v-model="dialogVisible"
    :title="currentCouponId ? '修改优惠券' : '新增优惠券'"
    width="600px"
    :close-on-click-modal="false"
  >
    <el-form :model="form" :rules="rules" ref="formRef" label-width="120px">
      <el-form-item label="优惠券名称" prop="couponName">
        <el-input v-model="form.couponName" placeholder="请输入优惠券名称"></el-input>
      </el-form-item>
      <el-form-item label="优惠券类型" prop="couponType">
        <el-radio-group v-model="form.couponType" @change="onCouponTypeChange">
          <el-radio :value="1">满减券</el-radio>
          <el-radio :value="2">折扣券</el-radio>
          <el-radio :value="3">无门槛券</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="使用门槛金额" prop="thresholdAmount" v-if="form.couponType !== 3">
        <el-input-number v-model="form.thresholdAmount" :min="0" :precision="2" :step="1" style="width: 200px">
          <template #suffix>元</template>
        </el-input-number>
      </el-form-item>
      <el-form-item label="优惠金额" prop="discountAmount" v-if="form.couponType !== 2">
        <el-input-number v-model="form.discountAmount" :min="0" :precision="2" :step="1" style="width: 200px">
          <template #suffix>元</template>
        </el-input-number>
      </el-form-item>
      <el-form-item label="折扣率" prop="discountRate" v-if="form.couponType === 2">
        <el-input-number v-model="form.discountRate" :min="0" :max="1" :precision="2" :step="0.05" style="width: 200px">
        </el-input-number>
        <div class="form-tip">如0.85表示85折</div>
      </el-form-item>
      <el-form-item label="发放总量" prop="totalCount">
        <el-input-number v-model="form.totalCount" :min="0" :step="1" style="width: 200px"></el-input-number>
        <div class="form-tip">0表示不限量</div>
      </el-form-item>
      <el-form-item label="有效期开始时间" prop="validStartTime">
        <el-date-picker
          v-model="form.validStartTime"
          type="datetime"
          placeholder="选择开始时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 200px"
        />
      </el-form-item>
      <el-form-item label="有效期结束时间" prop="validEndTime">
        <el-date-picker
          v-model="form.validEndTime"
          type="datetime"
          placeholder="选择结束时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 200px"
        />
      </el-form-item>
      <el-form-item label="上架秒杀" prop="rushingstatus">
        <el-switch v-model="form.rushingstatus" :active-value="1" :inactive-value="0"></el-switch>
      </el-form-item>
      <template v-if="form.rushingstatus === 1">
        <el-form-item label="秒杀开始时间" prop="rushingStartTime">
          <el-date-picker
            v-model="form.rushingStartTime"
            type="datetime"
            placeholder="选择秒杀开始时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 200px"
          />
        </el-form-item>
        <el-form-item label="秒杀结束时间" prop="rushingEndTime">
          <el-date-picker
            v-model="form.rushingEndTime"
            type="datetime"
            placeholder="选择秒杀结束时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 200px"
          />
        </el-form-item>
      </template>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, computed } from 'vue'
const { proxy } = getCurrentInstance()

const columns = [
  {
    label: '优惠券ID',
    prop: 'couponId',
    width: 150,
  },
  {
    label: '优惠券名称',
    prop: 'couponName',
    width: 180,
  },
  {
    label: '类型',
    prop: 'couponType',
    width: 100,
    scopedSlots: 'slotType',
  },
  {
    label: '门槛金额',
    prop: 'thresholdAmount',
    width: 100,
    formatter: (row) => row.thresholdAmount == 0 ? '无门槛' : '¥' + row.thresholdAmount,
  },
  {
    label: '优惠金额/折扣',
    prop: 'discountAmount',
    width: 140,
    scopedSlots: 'slotAmount',
  },
  {
    label: '剩余/总量',
    prop: 'remainCount',
    width: 120,
    scopedSlots: 'slotStock',
  },
  {
    label: '秒杀',
    prop: 'rushingstatus',
    width: 80,
    scopedSlots: 'slotRush',
  },
  {
    label: '状态',
    prop: 'status',
    width: 100,
    scopedSlots: 'slotStatus',
  },
  {
    label: '有效期',
    prop: 'validStartTime',
    width: 280,
    scopedSlots: 'slotValidTime',
  },
  {
    label: '操作',
    prop: 'op',
    width: 320,
    scopedSlots: 'slotOp',
  },
]

const warmupAllRush = () => {
  proxy.Confirm({
    message: '将为全部秒杀券预热 Redis 库存（以 DB 剩余量为准），确定继续？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.warmupRushStock,
        params: {},
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success(`预热完成，共 ${result.data ?? 0} 张券`)
      loadDataList()
    },
  })
}

const reconcileAllRush = () => {
  proxy.Confirm({
    message: '将以 DB 剩余库存为准对全部秒杀券 Redis 对账，确定继续？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.reconcileRushStock,
        params: {},
        showLoading: true,
      })
      if (!result) return
      const list = result.data || []
      proxy.Message.success(`对账完成，共处理 ${Array.isArray(list) ? list.length : 0} 张券`)
      loadDataList()
    },
  })
}

const warmupOne = (row) => {
  proxy.Confirm({
    message: `预热券「${row.couponName}」的 Redis 秒杀库存？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.warmupRushStock,
        params: { couponId: row.couponId },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('预热成功')
      loadDataList()
    },
  })
}

const reconcileOne = (row) => {
  proxy.Confirm({
    message: `对券「${row.couponName}」执行 Redis/DB 库存对账？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.reconcileRushStock,
        params: { couponId: row.couponId },
        showLoading: true,
      })
      if (!result) return
      const data = result.data
      const msg = data?.redisStockAfter != null
        ? `DB=${data.dbRemainCount}，Redis ${data.redisStockBefore}→${data.redisStockAfter}`
        : '对账完成'
      proxy.Message.success(msg)
      loadDataList()
    },
  })
}

const tableInfoRef = ref()
const searchForm = ref({})
const tableData = ref({})
const dialogVisible = ref(false)
const formRef = ref()
const currentCouponId = ref(null)

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

const loadDataList = async () => {
  let params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
  }
  Object.assign(params, searchForm.value)
  let result = await proxy.Request({
    url: proxy.Api.loadDiscountCoupon,
    params: params,
  })
  if (!result) {
    return
  }
  Object.assign(tableData.value, result.data)
}

const toggleStatus = (row) => {
  proxy.ConfirmSensitive({
    message: `确定要【${row.status == 0 ? '启用' : '停用'}】吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      let result = await proxy.Request({
        url: proxy.Api.updateDiscountCouponStatus,
        sensitiveConfirmPwd,
        params: {
          couponId: row.couponId,
          status: row.status == 0 ? 1 : 0,
        },
      })
      if (!result) {
        return
      }
      proxy.Message.success('操作成功')
      loadDataList()
    },
  })
}

const showEdit = async (couponId) => {
  currentCouponId.value = couponId
  dialogVisible.value = true
  if (formRef.value) {
    formRef.value.clearValidate()
  }

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
    let result = await proxy.Request({
      url: proxy.Api.getDiscountCouponInfo,
      params: {
        couponId: couponId,
      },
    })
    if (!result) {
      dialogVisible.value = false
      return
    }
    const data = result.data
    Object.assign(form, data)
  }
}

const onCouponTypeChange = () => {

  if (form.couponType === 2) {
    form.discountAmount = 0
  } else {
    form.discountRate = 0.9
  }

  if (form.couponType === 3) {
    form.thresholdAmount = 0
  }
}

const handleSave = async () => {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (valid) {
      let url = proxy.Api.saveDiscountCoupon

      const params = {
        ...form,
      }

      if (currentCouponId.value) {
        params.couponId = currentCouponId.value
      }

      let result = await proxy.Request({
        url: url,
        params: params,
      })
      if (!result) {
        return
      }
      proxy.Message.success('保存成功')
      dialogVisible.value = false
      loadDataList()
    }
  })
}
</script>

<style lang="scss" scoped>
.table-panel {
  height: calc(100vh - 135px);
}
.form-tip {
  font-size: 12px;
  color: #999;
  margin-left: 10px;
}

.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.text-muted {
  color: #999;
  font-size: 12px;
}
</style>
