<template>
  <Dialog :show="dialogConfig.show" :title="dialogConfig.title" :buttons="dialogConfig.buttons" width="80%"
    :showCancel="false" @close="close">
    <el-alert title="填写后，与可售库存联动。提交时，在最新可售库存基础上，增加或减少" type="warning" show-icon :closable="false" />
    <ProductSkuList :showType="1"></ProductSkuList>
  </Dialog>
  <ProductSkuBuild ref="productSkuBuildRef"></ProductSkuBuild>
</template>

<script setup>
import ProductSkuBuild from './ProductSkuBuild.vue'
import ProductSkuList from './ProductSkuList.vue'
import { ref, reactive, getCurrentInstance, nextTick } from 'vue'
const { proxy } = getCurrentInstance()
import { useProductEditStore } from '@/stores/productEditStore'
const productEditStore = useProductEditStore()


const dialogConfig = ref({
  show: false,
  title: '更新库存',
})
const productSkuBuildRef = ref()
const getProductInfo = async (productId, propertyValueIdHash) => {
  let result = await proxy.Request({
    url: proxy.Api.getProductInfo,
    params: {
      productId,
    },
  })
  if (!result) {
    return
  }
  productEditStore.productPropertyList = result.data.productPropertyList
  let skuList = result.data.skuList;
  
  if (propertyValueIdHash) {
    skuList = skuList.filter(sku => sku.propertyValueIdHash == propertyValueIdHash).map((sku) => [sku.propertyValueIdHash, sku])
  } else {
    skuList = skuList.map((sku) => [sku.propertyValueIdHash, sku])
  }
  productEditStore.skuData = new Map(skuList)
  productSkuBuildRef.value.generateSkuList()
}

const reset = () => {
  productEditStore.productPropertyList = []
  productEditStore.skuList = []
  productEditStore.skuData = new Map()
}

const show = async (productId, propertyValueIdHash) => {
  dialogConfig.value.show = true
  await nextTick()
  reset()
  getProductInfo(productId, propertyValueIdHash)
}

const close = () => {
  dialogConfig.value.show = false
}

defineExpose({
  show,
  close,
})
</script>

<style lang="scss" scoped></style>
