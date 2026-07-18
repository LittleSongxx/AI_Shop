<template>
  <div class="search-panel">
    <el-form :model="searchForm" @submit.prevent>
      <el-row :gutter="10">
        <el-col :span="5">
          <el-form-item label="商品名称">
            <el-input clearable placeholder="输入商品名称" v-model="searchForm.productNameFuzzy"></el-input>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="状态" prop="">
            <el-select clearable placeholder="请选择状态" v-model="searchForm.orderStatus">
              <el-option :value="item.status" :label="item.desc" v-for="item in orderStatusList"></el-option>
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
        <template #slotOrder="{ index, row }">
          <OrderItem :data="row" @delivery="deliveryHandler" @comment="commentHandler"></OrderItem>
        </template>
      </Table>
    </div>
  </el-card>

  <Delivery ref="deliveryRef" @reload="loadDataList"></Delivery>

  <CommentReply ref="commentRef" @reload="loadDataList"></CommentReply>
</template>

<script setup>
import CommentReply from './CommentReply.vue'
import Delivery from './Delivery.vue'
import OrderItem from './OrderItem.vue'
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

const orderStatusList = ref([])
const loadOrderStatus = async () => {
  let result = await proxy.Request({
    url: proxy.Api.loadOrderStatus,
  })
  if (!result) {
    return
  }
  orderStatusList.value = result.data
}
loadOrderStatus()

const columns = [
  {
    label: '订单信息',
    prop: 'avatar',
    scopedSlots: 'slotOrder',
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
    url: proxy.Api.loadOrder,
    params: params,
  })
  if (!result) {
    return
  }
  Object.assign(tableData.value, result.data)
}

const deliveryRef = ref()
const deliveryHandler = (data) => {
  deliveryRef.value.show(data.orderId)
}

const commentRef = ref()
const commentHandler = (orderId) => {
  commentRef.value.show(orderId)
}
</script>

<style lang="scss" scoped>
.table-panel {
  height: calc(100vh - 135px);
}
</style>
