<template>
  <div class="top-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="用户ID">
            <el-input clearable placeholder="用户ID" v-model="searchForm.userIdFuzzy" />
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="场景">
            <el-select clearable placeholder="全部" v-model="searchForm.scene">
              <el-option label="头像" value="avatar" />
              <el-option label="评论" value="comment" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="4">
          <el-form-item label="状态">
            <el-select clearable placeholder="全部" v-model="searchForm.status">
              <el-option label="待复核" :value="0" />
              <el-option label="已通过" :value="1" />
              <el-option label="确认违规" :value="2" />
              <el-option label="误报驳回" :value="3" />
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
        <template #slotImage="{ row }">
          <img v-if="row.imagePath" class="thumb" :src="imageUrl(row.imagePath)" alt="" />
          <span v-else>—</span>
        </template>
        <template #slotScene="{ row }">
          {{ row.scene === 'avatar' ? '头像' : row.scene === 'comment' ? '评论' : row.scene || '—' }}
        </template>
        <template #slotStatus="{ row }">
          <el-tag v-if="row.status === 0" type="warning" size="small">待复核</el-tag>
          <el-tag v-else-if="row.status === 1" type="success" size="small">已通过</el-tag>
          <el-tag v-else-if="row.status === 2" type="danger" size="small">确认违规</el-tag>
          <el-tag v-else-if="row.status === 3" type="info" size="small">误报驳回</el-tag>
        </template>
        <template #slotOperation="{ row }">
          <div class="list-op-panel">
            <OpBtn
              v-if="row.status === 0"
              icon="icon-edit"
              tips="复核"
              @click="openHandle(row)"
            />
            <OpBtn
              v-else
              icon="icon-user"
              tips="用户解封"
              @click="openHandle(row)"
            />
          </div>
        </template>
      </Table>
    </div>
  </el-card>
  <HandleImageModeration ref="handleRef" @reload="loadDataList" />
</template>

<script setup>
import HandleImageModeration from './HandleImageModeration.vue'
import { ref, reactive, getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()

const columns = [
  { label: 'ID', prop: 'recordId', width: 70 },
  { label: '用户ID', prop: 'userId', width: 120 },
  { label: 'IP', prop: 'userIp', width: 120 },
  { label: '场景', scopedSlots: 'slotScene', width: 80 },
  { label: '图片', scopedSlots: 'slotImage', width: 90 },
  { label: '百度结论', prop: 'conclusion', width: 160 },
  { label: '状态', scopedSlots: 'slotStatus', width: 90 },
  { label: '上传时间', prop: 'createTime', width: 160 },
  { label: '操作', scopedSlots: 'slotOperation', width: 80 }
]

const tableInfoRef = ref()
const handleRef = ref()
const searchForm = reactive({
  userIdFuzzy: '',
  scene: '',
  status: 0
})
const tableData = ref({})

const imageUrl = (path) => `${proxy.Api.sourcePath}${encodeURIComponent(path)}`

const loadDataList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize
  }
  if (searchForm.userIdFuzzy) params.userIdFuzzy = searchForm.userIdFuzzy
  if (searchForm.scene) params.scene = searchForm.scene
  if (searchForm.status !== undefined && searchForm.status !== '') params.status = searchForm.status
  const result = await proxy.Request({
    url: proxy.Api.imageModerationLoadList,
    params
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const openHandle = (row) => {
  handleRef.value?.show(row)
}
</script>

<style lang="scss" scoped>
.thumb {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 6px;
  background: #f0f0f0;
}
</style>
