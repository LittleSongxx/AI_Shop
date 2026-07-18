<template>
  <div class="top-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="幂等键">
            <el-input clearable placeholder="模糊搜索" v-model="searchForm.idempotencyKeyFuzzy" />
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="场景">
            <el-select clearable placeholder="全部" v-model="searchForm.bizScene">
              <el-option label="RAG" value="RAG" />
              <el-option label="通知" value="NOTIFY" />
              <el-option label="足迹" value="BROWSE" />
              <el-option label="签到" value="SIGN" />
              <el-option label="订单" value="PAY" />
              <el-option label="消费失败" value="CONSUME" />
              <el-option label="其他" value="OTHER" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="状态">
            <el-select clearable placeholder="全部状态" v-model="searchForm.status">
              <el-option label="待处理" :value="0" />
              <el-option label="处理中" :value="1" />
              <el-option label="已重放成功" :value="2" />
              <el-option label="重放失败" :value="3" />
              <el-option label="已忽略" :value="4" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <div class="table-panel">
      <Table
        ref="tableInfoRef"
        :columns="columns"
        :fetch="loadDataList"
        :dataSource="tableData"
        @rowClick="openHandle"
      >
        <template #slotStatus="{ row }">
          <el-tag v-if="row.status === 0" type="warning" size="small">待处理</el-tag>
          <el-tag v-else-if="row.status === 1" type="info" size="small">处理中</el-tag>
          <el-tag v-else-if="row.status === 2" type="success" size="small">已重放成功</el-tag>
          <el-tag v-else-if="row.status === 3" type="danger" size="small">重放失败</el-tag>
          <el-tag v-else-if="row.status === 4" size="small">已忽略</el-tag>
        </template>
        <template #slotPayload="{ row }">
          <el-link type="primary" :underline="false" @click.stop="openHandle(row)">
            {{ payloadPreview(row.payloadJson) }}
          </el-link>
        </template>
        <template #slotOperation="{ row }">
          <div class="list-op-panel">
            <OpBtn icon="icon-edit" tips="处理" @click="openHandle(row)" />
          </div>
        </template>
      </Table>
    </div>
  </el-card>

  <el-dialog v-model="dialogVisible" title="MQ 补偿详情" width="680px" destroy-on-close>
    <el-descriptions v-if="currentRow" :column="1" border size="small">
      <el-descriptions-item label="日志ID">{{ currentRow.logId }}</el-descriptions-item>
      <el-descriptions-item label="幂等键">{{ currentRow.idempotencyKey }}</el-descriptions-item>
      <el-descriptions-item label="场景">{{ currentRow.bizScene }}</el-descriptions-item>
      <el-descriptions-item label="交换机">{{ currentRow.exchange }}</el-descriptions-item>
      <el-descriptions-item label="路由键">{{ currentRow.routingKey }}</el-descriptions-item>
      <el-descriptions-item label="失败原因">{{ currentRow.errorMessage || '—' }}</el-descriptions-item>
      <el-descriptions-item label="消息体">
        <pre class="payload-pre">{{ currentRow.payloadJson }}</pre>
      </el-descriptions-item>
    </el-descriptions>
    <el-form label-width="88px" style="margin-top: 12px">
      <el-form-item label="处理状态">
        <el-select v-model="handleForm.status" placeholder="选择状态">
          <el-option label="待处理" :value="0" />
          <el-option label="处理中" :value="1" />
          <el-option label="已重放成功" :value="2" />
          <el-option label="重放失败" :value="3" />
          <el-option label="已忽略" :value="4" />
        </el-select>
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="handleForm.handleRemark" type="textarea" :rows="3" maxlength="512" show-word-limit />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="warning" :loading="replaying" @click="doReplay">触发重放</el-button>
      <el-button type="primary" :loading="saving" @click="saveStatus">保存状态</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()

const columns = [
  { label: 'ID', prop: 'logId', width: 70 },
  { label: '场景', prop: 'bizScene', width: 80 },
  { label: '幂等键', prop: 'idempotencyKey', width: 180 },
  { label: '路由键', prop: 'routingKey', width: 120 },
  { label: '级别', prop: 'reliabilityLevel', width: 80 },
  { label: '失败原因', prop: 'errorMessage', width: 160 },
  { label: '消息体', scopedSlots: 'slotPayload', width: 240 },
  { label: '重试', prop: 'retryCount', width: 60 },
  { label: '状态', scopedSlots: 'slotStatus', width: 100 },
  { label: '创建时间', prop: 'createTime', width: 160 },
  { label: '操作', scopedSlots: 'slotOperation', width: 70 }
]

const tableInfoRef = ref()
const tableData = ref({})
const dialogVisible = ref(false)
const currentRow = ref(null)
const saving = ref(false)
const replaying = ref(false)

const searchForm = reactive({
  idempotencyKeyFuzzy: '',
  bizScene: '',
  status: ''
})

const payloadPreview = (payload) => {
  if (!payload) return '（空）点击查看'
  const text = String(payload).replace(/\s+/g, ' ').trim()
  return text.length > 48 ? `${text.slice(0, 48)}…` : text
}

const handleForm = reactive({
  status: 0,
  handleRemark: ''
})

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize
  }
  if (searchForm.idempotencyKeyFuzzy) params.idempotencyKeyFuzzy = searchForm.idempotencyKeyFuzzy
  if (searchForm.bizScene) params.bizScene = searchForm.bizScene
  if (searchForm.status !== undefined && searchForm.status !== null && searchForm.status !== '') {
    params.status = searchForm.status
  }
  const result = await proxy.Request({
    url: proxy.Api.mqCompensationLogLoadList,
    params
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const openHandle = (row) => {
  currentRow.value = row
  handleForm.status = row.status ?? 0
  handleForm.handleRemark = row.handleRemark || ''
  dialogVisible.value = true
}

const saveStatus = async () => {
  if (!currentRow.value) return
  saving.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.mqCompensationLogUpdateStatus,
      params: {
        logId: currentRow.value.logId,
        status: handleForm.status,
        handleRemark: handleForm.handleRemark
      }
    })
    if (result) {
      proxy.Message.success('已保存')
      dialogVisible.value = false
      loadDataList()
    }
  } finally {
    saving.value = false
  }
}

const doReplay = async () => {
  if (!currentRow.value) return
  replaying.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.mqCompensationLogReplay,
      params: { logId: currentRow.value.logId }
    })
    if (result) {
      proxy.Message.success('重放已提交')
      dialogVisible.value = false
      loadDataList()
    }
  } finally {
    replaying.value = false
  }
}
</script>

<style scoped>
.payload-pre {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}
</style>
