<template>
  <div class="top-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="4">
          <el-form-item label="数据来源">
            <el-select clearable placeholder="全部" v-model="searchForm.source">
              <el-option label="全部（DB）" value="" />
              <el-option label="发送失败" value="SEND" />
              <el-option label="消费失败" value="CONSUME" />
              <el-option label="Redis 死信快照" value="REDIS_DLQ" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="类型">
            <el-select clearable placeholder="全部" v-model="searchForm.dataType">
              <el-option label="商品 PRODUCT" value="PRODUCT" />
              <el-option label="FAQ" value="FAQ" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="dataId">
            <el-input clearable placeholder="模糊搜索" v-model="searchForm.dataIdFuzzy" />
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="状态">
            <el-select clearable placeholder="全部" v-model="searchForm.status">
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
    <el-alert type="info" show-icon :closable="false" class="tip-alert">
      发送失败：RAG 消息未能投递到 MQ；消费失败：向量写入失败且重试耗尽；Redis 快照为 DLQ 热索引（DB 审查表为持久兜底）。
    </el-alert>
  </div>
  <el-card class="table-data-card">
    <div class="table-panel">
      <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
        <template #slotSource="{ row }">
          <el-tag v-if="row.source === 'SEND'" type="warning" size="small">发送</el-tag>
          <el-tag v-else-if="row.source === 'CONSUME'" type="danger" size="small">消费</el-tag>
          <el-tag v-else-if="row.source === 'REDIS_DLQ'" size="small">Redis</el-tag>
          <span v-else>{{ row.source || '—' }}</span>
        </template>
        <template #slotStatus="{ row }">
          <el-tag v-if="row.status === 0" type="warning" size="small">待处理</el-tag>
          <el-tag v-else-if="row.status === 1" type="info" size="small">处理中</el-tag>
          <el-tag v-else-if="row.status === 2" type="success" size="small">已重放成功</el-tag>
          <el-tag v-else-if="row.status === 3" type="danger" size="small">重放失败</el-tag>
          <el-tag v-else-if="row.status === 4" size="small">已忽略</el-tag>
          <span v-else>—</span>
        </template>
        <template #slotOperation="{ row }">
          <div class="list-op-panel">
            <OpBtn v-if="row.logId" icon="icon-edit" tips="处理" @click="openHandle(row)" />
            <OpBtn v-if="row.source === 'REDIS_DLQ'" icon="icon-delete" tips="清除快照" @click="dismissRedis(row)" />
          </div>
        </template>
      </Table>
    </div>
  </el-card>

  <el-dialog v-model="dialogVisible" title="RAG 同步失败处理" width="560px" destroy-on-close>
    <el-descriptions v-if="currentRow" :column="1" border size="small">
      <el-descriptions-item label="日志ID">{{ currentRow.logId || '—' }}</el-descriptions-item>
      <el-descriptions-item label="来源">{{ sourceLabel(currentRow.source) }}</el-descriptions-item>
      <el-descriptions-item label="dataId">{{ currentRow.dataId || '—' }}</el-descriptions-item>
      <el-descriptions-item label="类型">{{ currentRow.dataType || '—' }}</el-descriptions-item>
      <el-descriptions-item label="队列">{{ currentRow.queueName || '—' }}</el-descriptions-item>
      <el-descriptions-item label="失败原因">{{ currentRow.errorMessage || '—' }}</el-descriptions-item>
      <el-descriptions-item label="消息体">
        <pre class="payload-pre">{{ currentRow.payloadJson || '—' }}</pre>
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
  { label: '来源', scopedSlots: 'slotSource', width: 80 },
  { label: 'dataId', prop: 'dataId', width: 120 },
  { label: '类型', prop: 'dataType', width: 90 },
  { label: '队列', prop: 'queueName', width: 130 },
  { label: '失败原因', prop: 'errorMessage', width: 200 },
  { label: '重试', prop: 'retryCount', width: 60 },
  { label: '状态', scopedSlots: 'slotStatus', width: 100 },
  { label: '失败时间', prop: 'createTime', width: 160 },
  { label: '操作', scopedSlots: 'slotOperation', width: 90 }
]

const tableInfoRef = ref()
const tableData = ref({})
const dialogVisible = ref(false)
const currentRow = ref(null)
const saving = ref(false)
const replaying = ref(false)

const searchForm = reactive({
  source: '',
  dataType: '',
  dataIdFuzzy: '',
  status: 0
})

const handleForm = reactive({
  status: 0,
  handleRemark: ''
})

const sourceLabel = (s) => {
  if (s === 'SEND') return '发送失败'
  if (s === 'CONSUME') return '消费失败'
  if (s === 'REDIS_DLQ') return 'Redis 死信快照'
  return s || '—'
}

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize
  }
  if (searchForm.source) params.source = searchForm.source
  if (searchForm.dataType) params.dataType = searchForm.dataType
  if (searchForm.dataIdFuzzy) params.dataIdFuzzy = searchForm.dataIdFuzzy
  if (searchForm.status !== undefined && searchForm.status !== '') params.status = searchForm.status
  const result = await proxy.Request({
    url: proxy.Api.ragSyncFailureLoadList,
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
  if (!currentRow.value?.logId) return
  saving.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.ragSyncFailureUpdateStatus,
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
  if (!currentRow.value?.logId) return
  replaying.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.ragSyncFailureReplay,
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

const dismissRedis = async (row) => {
  const result = await proxy.Request({
    url: proxy.Api.ragSyncFailureDismissRedis,
    params: { dataId: row.dataId, dataType: row.dataType },
    showLoading: true
  })
  if (result) {
    proxy.Message.success('已清除 Redis 快照')
    loadDataList()
  }
}
</script>

<style scoped>
.tip-alert {
  margin-top: 8px;
}
.payload-pre {
  margin: 0;
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}
</style>
