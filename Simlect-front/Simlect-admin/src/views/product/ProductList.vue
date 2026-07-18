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
            <el-select clearable placeholder="请选择状态" v-model="searchForm.status">
              <el-option :value="0" label="未上架"></el-option>
              <el-option :value="1" label="已上架"></el-option>
              <el-option :value="-1" label="已删除"></el-option>
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-form-item label="推荐" prop="">
            <el-select clearable placeholder="请选择推荐类型" v-model="searchForm.commendType">
              <el-option :value="0" label="未推荐"></el-option>
              <el-option :value="1" label="已推荐"></el-option>
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="5">
          <el-button type="primary" @click="loadDataList">搜索</el-button>
          <el-button @click="proxy.Utils.jump('/product/addProduct')" type="success">发布商品</el-button>
        </el-col>
      </el-row>
    </el-form>
  </div>
  <el-card class="table-data-card">
    <div class="table-panel">
      <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData">
        <template #slotProduct="{ index, row }">
          <div class="product-info-panel">
            <Cover :source="row.cover.split(',')[0]" :width="70" class="cover"></Cover>
            <div class="product-info">
              <div class="product-name">{{ row.productName }}</div>
              <div class="product-id">ID:{{ row.productId }}</div>
              <div class="category-name">
                分类: {{ row.categoryName }}
              </div>
            </div>
          </div>
        </template>

        <template #slotStatus="{ index, row }">
          <el-tag v-if="row.status == 0" effect="dark" type="danger">未上架</el-tag>
          <el-tag v-if="row.status == 1" effect="dark" type="success">已上架</el-tag>
          <el-tag v-if="row.status == -1" effect="dark" type="danger">已删除</el-tag>
        </template>

        <template #slotPrice="{ index, row }">
          <div class="price-panel">
            <Price :price="row.minPrice" :size="16"></Price>
            <div class="line">~</div>
            <Price :price="row.maxPrice" :size="16"></Price>
          </div>
        </template>

        <template #slotStock="{ index, row }">
          <div v-if="row.status != -1">
            {{ row.totalStock }} <span class="iconfont icon-edit" @click="updateStock(row)"></span>
          </div>
        </template>

        <template #commend="{ index, row }">
          <el-tag v-if="row.commendType == 0" effect="dark" type="danger">未推荐</el-tag>
          <el-tag v-if="row.commendType == 1" effect="dark" type="success">已推荐</el-tag>
        </template>

        <template #slotOp="{ index, row }">
          <div class="list-op-panel" v-if="row.status != -1">
            <OpBtn icon="icon-view" tips="预览" @click="viewProduct(row)"></OpBtn>
            <OpBtn
              v-if="row.commendType == 0"
              icon="icon-commend"
              tips="推荐"
              :disabled="!canCommend(row)"
              :disabled-tips="getCommendBlockReason(row)"
              @click="commend(row)"
            />
            <OpBtn
              v-if="row.commendType == 1"
              icon="icon-cancel-commend"
              tips="取消推荐"
              @click="commend(row)"
            />
            <OpBtn icon="icon-stock" tips="更新库存" @click="updateStock(row)"></OpBtn>
            <OpBtn
              :icon="row.status == 0 ? 'icon-shangjia' : 'icon-xiajia'"
              :tips="row.status == 0 ? '上架' : '下架'"
              :disabled="row.status == 1 && !canDelist(row)"
              :disabled-tips="getDelistBlockReason(row)"
              @click="changeStatus(row)"
            />
            <OpBtn icon="icon-edit" tips="修改" @click="proxy.Utils.jump(`/product/updateProduct/${row.productId}`)">
            </OpBtn>
            <OpBtn
              icon="icon-delete"
              type="danger"
              tips="删除"
              :disabled="!canDelete(row)"
              :disabled-tips="getDeleteBlockReason(row)"
              @click="del(row)"
            />
          </div>
        </template>
      </Table>
    </div>
  </el-card>
  <ProductStock ref="productStockRef"></ProductStock>
  <ProductView ref="productViewRef"></ProductView>
</template>

<script setup>
import ProductView from './ProductView.vue'
import ProductStock from './edit/ProductStock.vue'
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
import { mitter } from '@/eventbus/eventBus.js'
import {
  canCommend,
  canDelist,
  canDelete,
  getCommendBlockReason,
  getDelistBlockReason,
  getDeleteBlockReason
} from '@/utils/productRules.js'

const columns = [
  {
    label: '商品信息',
    prop: 'avatar',
    scopedSlots: 'slotProduct',
  },
  {
    label: '价格区间',
    prop: 'price',
    width: 200,
    scopedSlots: 'slotPrice',
  },
  {
    label: '总库存',
    prop: 'stock',
    width: 100,
    scopedSlots: 'slotStock',
  },
  {
    label: 'SKU数量',
    prop: 'skuCount',
    width: 100,
  },
  {
    label: '状态',
    prop: 'status',
    scopedSlots: 'slotStatus',
    width: 100,
  },
  {
    label: '推荐',
    prop: 'commend',
    scopedSlots: 'commend',
    width: 100,
  },
  {
    label: '操作',
    prop: 'op',
    width: 380,
    scopedSlots: 'slotOp',
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
    url: proxy.Api.loadProduct,
    params: params,
  })
  if (!result) {
    return
  }
  Object.assign(tableData.value, result.data)
}

const del = (row) => {
  const block = getDeleteBlockReason(row)
  if (block) {
    proxy.Message.warning(block)
    return
  }
  proxy.ConfirmSensitive({
    message: `确定要删除【${row.productName}】吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      let result = await proxy.Request({
        url: proxy.Api.deleteProduct,
        sensitiveConfirmPwd,
        params: {
          productId: row.productId,
        },
      })
      if (!result) {
        return
      }
      proxy.Message.success('操作成功')
      loadDataList()
    },
  })
}

const changeStatus = (row) => {
  if (row.status == 1) {
    const block = getDelistBlockReason(row)
    if (block) {
      proxy.Message.warning(block)
      return
    }
  }
  proxy.ConfirmSensitive({
    message: `确定要【${row.status == 0 ? '上架' : '下架'}】吗？`,
    okfun: async (sensitiveConfirmPwd) => {
      let result = await proxy.Request({
        url: proxy.Api.updateProductStatus,
        sensitiveConfirmPwd,
        params: {
          productId: row.productId,
          status: row.status == 0 ? 1 : 0,
        },
      })
      if (!result) {
        return
      }
      proxy.Message.success('操作成功')
      loadDataList()
    },
  })
}

const commend = (row) => {
  if (row.commendType == 0) {
    const block = getCommendBlockReason(row)
    if (block) {
      proxy.Message.warning(block)
      return
    }
  }
  proxy.Confirm({
    message: `确定要【${row.commendType == 0 ? '推荐' : '取消推荐'}】吗？`,
    okfun: async () => {
      let result = await proxy.Request({
        url: proxy.Api.commendProduct,
        params: {
          productId: row.productId,
          commendType: row.commendType == 0 ? 1 : 0,
        },
      })
      if (!result) {
        return
      }
      proxy.Message.success('操作成功')
      loadDataList()
    },
  })
}

const productStockRef = ref()
const updateStock = (row) => {
  productStockRef.value.show(row.productId)
}

const updateStockHandler = ({ productId, totalStock }) => {
  const row = tableData.value.list.find((item) => {
    return item.productId == productId
  })
  row.totalStock = totalStock
}

const productViewRef = ref()
const viewProduct = (row) => {
  productViewRef.value.show(row.productId)
}

onMounted(() => {
  mitter.on('updateStockCallback', updateStockHandler)
})

onUnmounted(() => {
  mitter.off('updateStockCallback')
})
</script>

<style lang="scss" scoped>
.table-panel {
  height: calc(100vh - 135px);

  .product-info-panel {
    display: flex;

    .cover {
      margin-right: 10px;
    }

    .product-info {
      display: flex;
      flex-direction: column;

      .product-name {
        font-size: 16px;
      }

      .product-id {
        margin-top: 3px;
        font-size: 12px;
        color: #999999;
      }

      .category-name {
        font-size: 13px;
        color: #999999;
      }
    }
  }

  .price-panel {
    display: flex;
    align-items: center;

    .line {
      color: var(--text3);
      margin: 0px 10px;
    }
  }

  .icon-edit {
    cursor: pointer;
  }

  .list-op-panel {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
    max-width: 100%;
  }
}
</style>
