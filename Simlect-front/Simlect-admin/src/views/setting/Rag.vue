<template>

  <div class="search-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="问题">
            <el-input clearable placeholder="输入问题" v-model="searchForm.questionFuzzy"></el-input>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
          <el-button type="success" @click="showEdit()">新增</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card>
    <div class="table-panel">
      <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
        <template #slotSimilarQuestion="{ row }">
          <div v-for="(item,index) in row.similarQuestion">{{index+1}}、{{item}}</div>
        </template>
        <template #slotOperation="{ index, row }">
          <a href="javascript:void(0)" class="a-link" @click="showEdit(row)">修改</a>
          <el-divider direction="vertical" />
          <a href="javascript:void(0)" class="a-link" @click="delRag(row)">删除</a>
        </template>
      </Table>
    </div>
  </el-card>
  <RagEdit ref="ragEditRef" @reload="loadDataList"></RagEdit>
</template>

<script setup>
import RagEdit from './RagEdit.vue'
import {
  ref,
  reactive,
  getCurrentInstance,
  nextTick,
  onMounted,
  onUnmounted,
} from 'vue'
import { useRouter } from 'vue-router'
const { proxy } = getCurrentInstance()

const columns = [
  {
    label: '问题',
    prop: 'question',
    width: 300,
  },
  {
    label: '相似问题',
    prop: 'question',
    scopedSlots: 'slotSimilarQuestion',
  },
  {
    label: '创建时间',
    prop: 'createTime',
    width: 200,
  },
  {
    label: '操作',
    prop: 'op',
    scopedSlots: 'slotOperation',
    width: 100,
  },
]

const tableInfoRef = ref()
const searchForm = ref({})
const tableData = ref({})
const loadDataList = async () => {
  let params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
  }
  Object.assign(params, searchForm.value)
  let result = await proxy.Request({
    url: proxy.Api.loadRagQuestion,
    params: params,
  })
  if (!result) {
    return
  }
  result.data.list.map((item) => {
    if (item.similarQuestion) {
      item.similarQuestion = JSON.parse(item.similarQuestion)
    }
    return item
  })
  Object.assign(tableData.value, result.data)
}

const ragEditRef = ref()
const showEdit = (data) => {
  ragEditRef.value.show(data)
}

const delRag = (data) => {
  proxy.Confirm({
    message: '确定要删除吗?',
    okfun: async () => {
      let result = await proxy.Request({
        url: proxy.Api.delRagQuestion,
        params: {
          questionId: data.questionId,
        },
      })
      if (!result) {
        return
      }
      proxy.Message.success('删除成功')
      loadDataList()
    },
  })
}
</script>

<style lang="scss" scoped>
.table-panel {
  height: calc(100vh - 135px);
}
</style>
