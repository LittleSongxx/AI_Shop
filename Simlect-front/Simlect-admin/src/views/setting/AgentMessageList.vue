<template>
  <div class="search-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="用户ID">
            <el-input v-model="searchForm.userId" clearable placeholder="用户ID" />
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="业务类型">
            <el-input v-model="searchForm.bizType" clearable placeholder="如 product_search" />
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <Table ref="tableRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
      <template #slotStatus="{ row }">
        <el-tag v-if="row.status === 0" type="info">已取消</el-tag>
        <el-tag v-else-if="row.status === 1" type="warning">回答中</el-tag>
        <el-tag v-else type="success">完成</el-tag>
      </template>
      <template #slotUser="{ row }">
        <p class="msg-line user">{{ row.userMessage || '—' }}</p>
      </template>
      <template #slotAi="{ row }">
        <p class="msg-line ai">{{ clipText(row.assistantMessage) }}</p>
      </template>
      <template #slotOp="{ row }">
        <div class="list-op-panel">
          <OpBtn icon="icon-delete" type="danger" tips="删除" @click="delRow(row)" />
        </div>
      </template>
    </Table>
  </el-card>
</template>

<script setup>
import { getCurrentInstance, ref } from 'vue'

const { proxy } = getCurrentInstance()
const tableRef = ref()
const tableData = ref({})
const searchForm = ref({ userId: '', bizType: '' })

const clipText = (text) => {
  if (!text) return '—'
  const s = String(text)
  return s.length > 120 ? `${s.slice(0, 120)}…` : s
}

const columns = [
  { label: 'ID', prop: 'messageId', width: 80 },
  { label: '用户ID', prop: 'userId', width: 120 },
  { label: '状态', prop: 'status', width: 90, scopedSlots: 'slotStatus' },
  { label: '业务类型', prop: 'bizType', width: 140 },
  { label: '用户消息', prop: 'userMessage', scopedSlots: 'slotUser' },
  { label: 'AI回复', prop: 'assistantMessage', scopedSlots: 'slotAi' },
  { label: '时间', prop: 'sendTime', width: 170 },
  { label: '操作', prop: 'op', width: 80, scopedSlots: 'slotOp' },
]

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
    orderBy: 'send_time desc',
  }
  if (searchForm.value.userId) params.userId = searchForm.value.userId
  if (searchForm.value.bizType) params.bizType = searchForm.value.bizType
  const result = await proxy.Request({
    url: proxy.Api.agentMessageLoadList,
    params,
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除对话记录 #${row.messageId} 吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.agentMessageDelete,
        params: { messageId: row.messageId },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('已删除')
      loadDataList()
    },
  })
}
</script>

<style scoped lang="scss">
.msg-line {
  margin: 0;
  font-size: 13px;
  line-height: 1.45;
  text-align: left;
  word-break: break-word;

  &.user {
    color: #333;
  }

  &.ai {
    color: #666;
  }
}

.list-op-panel {
  display: inline-flex;
  gap: 6px;
}
</style>
