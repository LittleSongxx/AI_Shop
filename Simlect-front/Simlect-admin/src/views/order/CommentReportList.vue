<template>
  <div class="top-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="举报理由">
            <el-input clearable placeholder="输入举报理由" v-model="searchForm.reasonFuzzy"></el-input>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="状态">
            <el-select clearable placeholder="全部" v-model="searchForm.status">
              <el-option label="待处理" :value="0" />
              <el-option label="已处理" :value="1" />
              <el-option label="已驳回" :value="2" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <div class="table-panel">
      <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
        <template #slotCommentSnapshot="{ index, row }">
          <div class="snapshot-text">{{ row.commentSnapshot || '--' }}</div>
        </template>

        <template #slotStatus="{ index, row }">
          <el-tag v-if="row.status === 0" type="warning" size="small">待处理</el-tag>
          <el-tag v-else-if="row.status === 1" type="success" size="small">已处理</el-tag>
          <el-tag v-else-if="row.status === 2" type="info" size="small">已驳回</el-tag>
        </template>

        <template #slotOperation="{ index, row }">
          <div class="list-op-panel">
            <OpBtn
              v-if="row.status === 0"
              icon="icon-edit"
              tips="处理"
              @click="handleHandler(row)"
            />
            <OpBtn icon="icon-delete" type="danger" tips="删除" @click="delReport(row)" />
          </div>
        </template>
      </Table>
    </div>
  </el-card>
  <HandleReport ref="handleRef" @reload="loadDataList"></HandleReport>
</template>

<script setup>
import HandleReport from './HandleReport.vue'
import { ref, reactive, getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()

const columns = [
  {
    label: '订单号',
    prop: 'orderId',
    width: 180,
  },
  {
    label: '举报人',
    prop: 'reporterUserId',
    width: 120,
  },
  {
    label: '举报理由',
    prop: 'reason',
    width: 120,
  },
  {
    label: '补充说明',
    prop: 'detail',
    width: 180,
  },
  {
    label: '评论快照',
    scopedSlots: 'slotCommentSnapshot',
  },
  {
    label: '状态',
    prop: 'status',
    scopedSlots: 'slotStatus',
    width: 90,
  },
  {
    label: '举报时间',
    prop: 'reportTime',
    width: 160,
  },
  {
    label: '操作',
    prop: 'operation',
    width: 120,
    scopedSlots: 'slotOperation',
  },
]

const tableInfoRef = ref()
const searchForm = reactive({
  reasonFuzzy: '',
  status: undefined,
})
const tableData = ref({})

const loadDataList = async () => {
  let params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
  }
  if (searchForm.reasonFuzzy) params.reasonFuzzy = searchForm.reasonFuzzy
  if (searchForm.status !== undefined && searchForm.status !== '') params.status = searchForm.status
  let result = await proxy.Request({
    url: proxy.Api.loadCommentReport,
    params: params,
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const handleRef = ref()
const handleHandler = (row) => {
  handleRef.value.show(row)
}

const delReport = (row) => {
  proxy.Confirm({
    message: `确定要删除该举报记录吗？`,
    okfun: async () => {
      let result = await proxy.Request({
        url: proxy.Api.deleteCommentReport,
        params: { reportId: row.reportId },
      })
      if (!result) return
      proxy.Message.success('操作成功')
      loadDataList()
    },
  })
}
</script>

<style lang="scss" scoped>
.table-panel {
  height: calc(100vh - 135px);

  .snapshot-text {
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
