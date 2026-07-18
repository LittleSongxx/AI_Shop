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
          <el-form-item label="收货人">
            <el-input v-model="searchForm.addresseeFuzzy" clearable placeholder="收货人" />
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="手机号">
            <el-input v-model="searchForm.phoneFuzzy" clearable placeholder="手机号" />
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
      <template #slotDefault="{ row }">
        <el-tag v-if="row.defaultType == 1" type="success" size="small">默认</el-tag>
        <span v-else class="text-muted">—</span>
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
const searchForm = ref({ userId: '', addresseeFuzzy: '', phoneFuzzy: '' })

const columns = [
  { label: '地址ID', prop: 'addressId', width: 150 },
  { label: '用户ID', prop: 'userId', width: 120 },
  { label: '收货人', prop: 'addressee', width: 100 },
  { label: '手机号', prop: 'phone', width: 130 },
  { label: '详细地址', prop: 'address' },
  { label: '默认', prop: 'defaultType', width: 80, scopedSlots: 'slotDefault' },
  { label: '操作', prop: 'op', width: 80, scopedSlots: 'slotOp' },
]

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
    orderBy: 'address_id desc',
  }
  Object.assign(params, searchForm.value)
  const result = await proxy.Request({
    url: proxy.Api.userAddressLoadList,
    params,
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除用户 ${row.userId} 的地址吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.userAddressDelete,
        params: { addressId: row.addressId },
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
.text-muted {
  color: #999;
  font-size: 12px;
}

.list-op-panel {
  display: inline-flex;
  gap: 6px;
}
</style>
