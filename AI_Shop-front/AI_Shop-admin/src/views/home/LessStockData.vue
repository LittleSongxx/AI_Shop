<template>
  <Table ref="tableInfoRef" :columns="columns" :fetch="loadDataList" :dataSource="tableData" :showPagination="false">
    <template #slotProduct="{ index, row }">
      <div class="product-info-panel">
        <Cover :source="row.productCover" :width="70" class="cover"></Cover>
        <div class="product-info">
          <div class="product-name">{{ row.productName }}</div>
          <div class="product-id">ID:{{ row.productId }}</div>
          <div class="property-name">
            <div v-for="(item, index) in row.propertyData">
              {{ item.propertyName }}:{{ item.propertyValue }} <el-divider direction="vertical"
                v-if="index < row.propertyData.length - 1" />
            </div>

          </div>
        </div>
      </div>
    </template>
    <template #slotStock="{ index, row }">
      {{ row.stock }} <span class="iconfont icon-edit" @click="updateStock(row)"></span>
    </template>
  </Table>
  <ProductStock ref="productStockRef"></ProductStock>
</template>

<script setup>
import ProductStock from '@/views/product/edit/ProductStock.vue'
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

const columns = [
  {
    label: '库存预警',
    prop: 'avatar',
    scopedSlots: 'slotProduct',
  },
  {
    label: '库存',
    prop: 'stock',
    width: 100,
    scopedSlots: 'slotStock',
  },
]

const searchForm = ref({})
const tableData = ref({ pageNo: 1, pageSize: 4 })
const loadDataList = async () => {
  let params = {
    pageNo: tableData.value.pageNo || 1,
    pageSize: tableData.value.pageSize || 4,
  }
  Object.assign(params, searchForm.value)
  let result = await proxy.Request({
    url: proxy.Api.loadLessStockProduct,
    params: params,
  })
  if (!result) {
    return
  }
  Object.assign(tableData.value, result.data)
}

const productStockRef = ref()
const updateStock = (row) => {
  productStockRef.value.show(row.productId, row.propertyValueIdHash)
}

onMounted(() => {
  mitter.on('updateStockCallback', () => {
    productStockRef.value.close();
    loadDataList()
  })
})

onUnmounted(() => {
  mitter.off('updateStockCallback')
})
</script>

<style lang="scss" scoped>
.product-info-panel {
  display: flex;

  .cover {
    margin-right: 10px;
  }

  .product-info {
    display: flex;
    flex-direction: column;

    .product-name {
      font-size: 13px;
      line-height: 1.3;
    }

    .product-id {
      margin-top: 3px;
      font-size: 12px;
      color: #999999;
    }

    .property-name {
      font-size: 13px;
      color: #999999;
      display: flex;
      align-items: center;
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
</style>
