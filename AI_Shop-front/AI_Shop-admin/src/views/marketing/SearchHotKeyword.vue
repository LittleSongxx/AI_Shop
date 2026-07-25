<template>
  <div class="search-panel">
    <el-form @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="8">
          <el-form-item label="热搜词">
            <el-input v-model="form.keyword" clearable placeholder="输入热搜词" maxlength="100" />
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="排序">
            <el-input-number v-model="form.sort" :min="0" :max="9999" />
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="状态">
            <el-select v-model="form.status" style="width: 100%">
              <el-option :value="1" label="启用" />
              <el-option :value="0" label="停用" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="8" class="form-actions">
          <el-button type="primary" @click="save">保存</el-button>
          <el-button @click="resetForm">重置</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>

  <el-card class="table-data-card">
    <Table :columns="columns" :dataSource="tableData" :fetch="loadDataList">
      <template #slotStatus="{ row }">
        <el-tag v-if="row.status == 1" type="success">启用</el-tag>
        <el-tag v-else type="info">停用</el-tag>
      </template>
      <template #slotOp="{ row }">
        <div class="list-op-panel">
          <OpBtn icon="icon-edit" tips="编辑" @click="editRow(row)" />
          <OpBtn icon="icon-delete" type="danger" tips="删除" @click="delRow(row)" />
        </div>
      </template>
    </Table>
  </el-card>
</template>

<script setup>
import { getCurrentInstance, reactive, ref } from 'vue'

const { proxy } = getCurrentInstance()
const tableData = ref({ list: [], pageNo: 1, pageTotal: 1 })
const form = reactive({ keyword: '', sort: 0, status: 1 })

const columns = [
  { label: '热搜词', prop: 'keyword' },
  { label: '排序', prop: 'sort', width: 100 },
  { label: '状态', prop: 'status', width: 100, scopedSlots: 'slotStatus' },
  { label: '更新时间', prop: 'updateTime', width: 180 },
  { label: '操作', prop: 'op', width: 140, scopedSlots: 'slotOp' },
]

const loadDataList = async () => {
  const result = await proxy.Request({ url: proxy.Api.searchHotKeywordLoadList })
  if (!result) return
  tableData.value = { list: result.data || [], pageNo: 1, pageTotal: 1 }
}

const resetForm = () => {
  form.keyword = ''
  form.sort = 0
  form.status = 1
}

const editRow = (row) => {
  form.keyword = row.keyword
  form.sort = row.sort ?? 0
  form.status = row.status ?? 1
}

const save = async () => {
  if (!form.keyword?.trim()) {
    proxy.Message.warning('请输入热搜词')
    return
  }
  const result = await proxy.Request({
    url: proxy.Api.searchHotKeywordSave,
    params: {
      keyword: form.keyword.trim(),
      sort: form.sort,
      status: form.status,
    },
    showLoading: true,
  })
  if (!result) return
  proxy.Message.success('保存成功')
  resetForm()
  loadDataList()
}

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除热搜词「${row.keyword}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.searchHotKeywordDel,
        params: { keyword: row.keyword },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('已删除')
      loadDataList()
    },
  })
}

loadDataList()
</script>

<style scoped lang="scss">
.form-actions {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding-bottom: 4px;
}
</style>
