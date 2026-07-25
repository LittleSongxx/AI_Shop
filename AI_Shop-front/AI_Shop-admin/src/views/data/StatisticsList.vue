<template>
  <div class="search-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="统计日期">
            <el-input v-model="searchForm.statisticsDate" clearable placeholder="如 2026-05-30" />
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="数据类型">
            <el-select v-model="searchForm.dataType" clearable placeholder="全部" style="width: 100%">
              <el-option v-for="item in dataTypeOptions" :key="item.type" :value="item.type" :label="item.desc" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="8" class="toolbar-actions">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
          <el-button type="warning" plain @click="syncStatistics">手动同步统计</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <Table ref="tableRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
      <template #slotType="{ row }">
        {{ dataTypeLabel(row.dataType) }}
      </template>
      <template #slotValue="{ row }">
        {{ row.dataValue }}
      </template>
    </Table>
  </el-card>
</template>

<script setup>
import { getCurrentInstance, ref } from 'vue'

const { proxy } = getCurrentInstance()
const tableRef = ref()
const tableData = ref({})
const searchForm = ref({ statisticsDate: '', dataType: null })

const dataTypeOptions = [
  { type: 1, desc: '销售金额' },
  { type: 2, desc: '订单数量' },
  { type: 3, desc: '退款金额' },
  { type: 4, desc: '退款数量' },
]

const dataTypeLabel = (type) => dataTypeOptions.find((i) => i.type === type)?.desc || type

const columns = [
  { label: '统计日期', prop: 'statisticsDate', width: 140 },
  { label: '数据类型', prop: 'dataType', width: 120, scopedSlots: 'slotType' },
  { label: '数值', prop: 'dataValue', scopedSlots: 'slotValue' },
]

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
  }
  if (searchForm.value.statisticsDate) {
    params.statisticsDate = searchForm.value.statisticsDate
  }
  if (searchForm.value.dataType != null && searchForm.value.dataType !== '') {
    params.dataType = searchForm.value.dataType
  }
  const result = await proxy.Request({
    url: proxy.Api.statisticsInfoLoadList,
    params,
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const syncStatistics = () => {
  proxy.Confirm({
    message: '将重新计算并写入统计数据，确定继续？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.toolStatistics,
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('同步成功')
      loadDataList()
    },
  })
}
</script>

<style scoped lang="scss">
.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
</style>
