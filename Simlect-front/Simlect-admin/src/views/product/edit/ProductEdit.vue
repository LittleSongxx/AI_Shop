<template>
  <div class="form-style" :class="{ 'is-mobile-admin': isMobileAdmin }">

    <template v-if="isMobileAdmin">
      <div class="m-product-edit">
        <div class="m-edit-steps glass-card glass-strong">
          <button
            type="button"
            :class="['step', activeName === 'base' ? 'active' : '']"
            @click="activeName = 'base'"
          >
            基础信息
          </button>
          <button type="button" :class="['step', activeName === 'sku' ? 'active' : '']" @click="switchToSku">
            SKU 规格
          </button>
        </div>

        <div v-show="activeName === 'base'">
          <ProductBase :productInfo="productInfo"></ProductBase>
        </div>
        <div v-show="activeName === 'sku'" class="m-sku-layout content-panel">
          <ProductSkuProperty></ProductSkuProperty>
          <ProductSkuList></ProductSkuList>
        </div>
      </div>

      <div class="m-edit-footer">
        <button type="button" class="footer-cancel" @click="cancelPost">取消</button>
        <button type="button" class="footer-submit" @click="submitProduct()">
          {{ route.params.productId ? '保存商品' : '发布商品' }}
        </button>
      </div>
    </template>

    <template v-else>
      <el-tabs v-model="activeName" @tab-click="tabClick">
        <el-tab-pane label="基础信息" name="base">
          <ProductBase :productInfo="productInfo"></ProductBase>
        </el-tab-pane>
        <el-tab-pane label="SKU信息" name="sku">
          <div class="content-panel">
            <ProductSkuProperty></ProductSkuProperty>
            <ProductSkuList></ProductSkuList>
          </div>
        </el-tab-pane>
      </el-tabs>
      <div class="post-panel">
        <el-button @click="cancelPost" link>取消</el-button>
        <el-button @click="submitProduct()" type="primary">发布商品</el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import ProductSkuList from './ProductSkuList.vue'
import ProductSkuProperty from './ProductSkuProperty.vue'
import ProductBase from './ProductBase.vue'
import { ref, getCurrentInstance, computed, onMounted } from 'vue'
const { proxy } = getCurrentInstance()
import { useRouter, useRoute } from 'vue-router'
const router = useRouter()
const route = useRoute()

import { useProductEditStore } from '@/stores/productEditStore'
const productEditStore = useProductEditStore()

const activeName = ref('base')
const isMobileAdmin = computed(() => route.path.startsWith('/m/'))
const productListPath = () => (isMobileAdmin.value ? '/m/product' : '/product')

const tabClick = async (e) => {
  if (e.paneName == 'sku' && !productInfo.value.categoryId) {
    proxy.Message.warning('请先选择分类')
    return
  }
}

const switchToSku = () => {
  if (!productInfo.value.categoryId) {
    proxy.Message.warning('请先选择分类')
    activeName.value = 'base'
    return
  }
  activeName.value = 'sku'
}

const productInfo = ref({
  cover: Array(proxy.productMainImageCount).fill(''),
})

const getProductInfo = async () => {
  if (!route.params.productId) {
    return
  }
  let result = await proxy.Request({
    url: proxy.Api.getProductInfo,
    params: {
      productId: route.params.productId,
    },
  })
  if (!result) {
    return
  }
  productInfo.value = {
    ...result.data.productInfo,
    cover: result.data.productInfo.cover.split(','),
  }
  productEditStore.productPropertyList = result.data.productPropertyList
  productEditStore.skuData = new Map(
    result.data.skuList.map((sku) => [sku.propertyValueIdHash, sku])
  )
  productEditStore.excludedSkuHashes = new Set()
}

const cancelPost = () => {
  router.push(productListPath())
}

const submitProduct = async (sensitiveConfirmPwd) => {
  // @click 可能传入 MouseEvent；仅字符串才视为已确认的管理员密码
  const confirmPwd = typeof sensitiveConfirmPwd === 'string' ? sensitiveConfirmPwd : undefined
  activeName.value = 'base'
  if (!productInfo.value.cover.every((item) => item !== '' && item != null)) {
    proxy.Message.warning('请上传商品主图')
    return
  }
  if (!productInfo.value.productName?.trim()) {
    proxy.Message.warning('请输入商品名称')
    return
  }

  if (!productInfo.value.productDesc?.trim()) {
    proxy.Message.warning('请输入商品描述')
    return
  }
  activeName.value = 'sku'
  if (productEditStore.skuList.length === 0) {
    proxy.Message.warning('请先设置SKU属性并生成SKU列表')
    return
  }

  for (const property of productEditStore.productPropertyList) {
    for (const [index, value] of property.propertyValues.entries()) {
      if (property.coverType === 1 && !value.propertyCover) {
        proxy.Message.warning(
          `请上传【${property.propertyName}】属性第(${index + 1})行的图片`
        )
        return
      }
      if (!value.propertyValue.trim()) {
        proxy.Message.warning(
          `请填写【${property.propertyName}】属性第(${index + 1})行的值`
        )
        return
      }
    }
  }

  for (const [index, sku] of productEditStore.skuList.entries()) {
    if (sku.price <= 0) {
      proxy.Message.warning(`请设置sku列表第(${index + 1})行的价格`)
      return
    }
  }

  const productInfoResultData = { ...productInfo.value }
  productInfoResultData.cover = productInfoResultData.cover.join(',')
  productInfoResultData.pCategoryId = productInfoResultData.categoryId[0]
  productInfoResultData.categoryId = productInfoResultData.categoryId[1]

  const productPropertyListResultData = []
  for (let property of productEditStore.productPropertyList) {
    for (let [index, propertyValue] of property.propertyValues.entries()) {
      const resultValue = {
        ...property,
        ...propertyValue,
        sort: index,
      }
      delete resultValue.categoryId
      delete resultValue.pCategoryId
      delete resultValue.propertyValues
      productPropertyListResultData.push(resultValue)
    }
  }

  const skuListResultData = []
  for (let [index, sku] of productEditStore.skuList.entries()) {
    skuListResultData.push({
      price: sku.price,
      stock: sku.stock,
      sort: index,
      propertyValueIdHash: sku.propertyValueIdHash,
      propertyValueIds: sku.propertyValueIds,
    })
  }

  const doSave = async (confirmPwd) => {
    let result = await proxy.Request({
      url: route.params.productId ? proxy.Api.updateProduct : proxy.Api.addProduct,
      dataType: 'json',
      sensitiveConfirmPwd: confirmPwd,
      params: {
        productInfo: productInfoResultData,
        productPropertyList: productPropertyListResultData,
        skuList: skuListResultData,
      },
    })
    if (!result) {
      return
    }
    proxy.Message.success('保存成功')
    router.push(productListPath())
  }

  if (route.params.productId && !confirmPwd) {
    proxy.ConfirmSensitive({
      message: '保存将更新商品价格与库存等信息，是否继续？',
      okfun: doSave,
    })
    return
  }

  await doSave(confirmPwd)
}

const reset = () => {
  productInfo.value = { cover: Array(proxy.productMainImageCount).fill('') }
  productEditStore.resetSkuState()
}

onMounted(() => {
  reset()
  getProductInfo()
})
</script>

<style lang="scss" scoped>
.form-style {
  position: relative;

  .post-panel {
    position: absolute;
    top: 3px;
    right: 20px;
  }

  .content-panel {
    display: flex;
    height: calc(100vh - 130px);
  }

  &.is-mobile-admin {
    .content-panel {
      flex-direction: column;
      height: auto;
      min-height: 0;
      gap: 12px;
    }
  }
}
</style>
