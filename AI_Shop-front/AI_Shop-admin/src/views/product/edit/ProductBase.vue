<template>

  <div v-if="isMobileAdmin" class="m-product-base">
    <section class="glass-card m-edit-section">
      <div class="section-head">
        <div>
          <h3 class="section-title">商品主图</h3>
          <p class="section-hint">最多 {{ proxy.productMainImageCount }} 张，建议首张作为封面</p>
        </div>
        <span class="section-badge">必填</span>
      </div>
      <div class="m-cover-grid">
        <ImageSelect
          v-for="(_, index) in proxy.productMainImageCount"
          :key="index"
          v-model="productInfo.cover[index]"
          :cutWidth="250"
          :width="100"
        />
      </div>
    </section>

    <section class="glass-card m-edit-section">
      <h3 class="section-title">基本信息</h3>
      <div class="m-field-block">
        <label class="m-field-label">商品名称</label>
        <el-input v-model="productInfo.productName" placeholder="请输入商品名称" clearable />
      </div>
      <div class="m-field-block">
        <label class="m-field-label">商品分类</label>
        <el-cascader
          v-model="productInfo.categoryId"
          :options="categoryList"
          :props="{ label: 'categoryName', value: 'categoryId' }"
          style="width: 100%"
          placeholder="请选择分类"
          @change="getProductPropertyList"
          :disabled="route.params.productId != null"
        />
      </div>
    </section>

    <section class="glass-card m-edit-section">
      <div class="section-head">
        <div>
          <h3 class="section-title">商品描述</h3>
          <p class="section-hint">支持 Markdown，用于详情页展示</p>
        </div>
      </div>
      <div class="product-desc is-mobile">
        <EditorMarkdown v-model="productInfo.productDesc"></EditorMarkdown>
      </div>
    </section>
  </div>

  <el-form v-else class="form-style" label-width="auto" @submit.prevent>
    <el-form-item label="主图">
      <div class="cover-list">
        <ImageSelect
          v-for="(_, index) in proxy.productMainImageCount"
          :key="index"
          v-model="productInfo.cover[index]"
          :cutWidth="250"
          :width="120"
        />
      </div>
    </el-form-item>
    <el-form-item label="商品名称" prop="productName">
      <el-input v-model="productInfo.productName" placeholder="请输入商品名称" clearable></el-input>
    </el-form-item>
    <el-form-item label="分类" prop="categoryIdArray">
      <el-cascader
        v-model="productInfo.categoryId"
        :options="categoryList"
        :props="{ label: 'categoryName', value: 'categoryId' }"
        :style="{ width: '300px' }"
        @change="getProductPropertyList"
        :disabled="route.params.productId != null"
      />
    </el-form-item>
    <el-form-item label="商品描述" prop="productDesc">
      <div class="product-desc">
        <EditorMarkdown v-model="productInfo.productDesc"></EditorMarkdown>
      </div>
    </el-form-item>
  </el-form>
</template>

<script setup>
import EditorMarkdown from '@/components/markdown/EditorMarkdown.vue'
import ImageSelect from '@/components/ImageSelect.vue'
import { ref, getCurrentInstance, onMounted, watch, computed } from 'vue'
const { proxy } = getCurrentInstance()
import { useRoute } from 'vue-router'
const route = useRoute()
const isMobileAdmin = computed(() => route.path.startsWith('/m/'))

import { useProductEditStore } from '@/stores/productEditStore'
const productEditStore = useProductEditStore()

const props = defineProps({
  productInfo: {
    type: Object,
    default: {},
  },
})

const categoryList = ref([])
const loadCategory = async () => {
  let result = await proxy.Request({
    url: proxy.Api.loadCategory,
    params: {
      queryProperty: true,
    },
  })
  if (!result) {
    return
  }
  categoryList.value = result.data
}

const createPropertyWithDefaultValue = (property, index = 0) => ({
  ...property,
  propertyValues: [
    {
      propertyValueId: `${Date.now()}${index}`,
      propertyCover: '',
      propertyValue: '',
      propertyRemark: '',
    },
  ],
})

const getProductPropertyList = (data) => {
  applyCategoryPropertyTemplates(data[data.length - 1], true)
}

const syncCategoryPropertyTemplates = (categoryId) => {
  if (!categoryId || !categoryList.value.length) return
  applyCategoryPropertyTemplates(categoryId, false)
}

const findPathToNode = (id, nodes, path = []) => {
  for (const node of nodes) {
    const nextPath = [...path, node]
    if (node.categoryId === id) return nextPath
    if (node.children?.length) {
      const found = findPathToNode(id, node.children, nextPath)
      if (found) return found
    }
  }
  return null
}

const collectCategoryPropertyTemplates = (categoryId) => {
  const id = Array.isArray(categoryId) ? categoryId[categoryId.length - 1] : categoryId
  if (!id || !categoryList.value.length) return []
  const path = findPathToNode(id, categoryList.value)
  if (!path?.length) return []
  const seen = new Set()
  const templates = []
  for (const node of path) {
    for (const property of node.productPropertyList || []) {
      if (!property?.propertyId || seen.has(property.propertyId)) continue
      seen.add(property.propertyId)
      templates.push(createPropertyWithDefaultValue(property, templates.length))
    }
  }
  return templates
}

const applyCategoryPropertyTemplates = (categoryId, resetActiveList = true) => {
  const templates = collectCategoryPropertyTemplates(categoryId)
  productEditStore.categoryPropertyTemplates = templates.map((item) => ({
    ...item,
    propertyValues: item.propertyValues.map((v) => ({ ...v })),
  }))
  if (resetActiveList) {
    productEditStore.productPropertyList = templates.map((item) => ({
      ...item,
      propertyValues: item.propertyValues.map((v) => ({ ...v })),
    }))
    productEditStore.skuData = new Map()
    productEditStore.excludedSkuHashes = new Set()
  }
}

onMounted(() => {
  loadCategory()
})

watch(
  () => [props.productInfo?.categoryId, categoryList.value.length],
  () => {
    syncCategoryPropertyTemplates(props.productInfo?.categoryId)
  }
)
</script>

<style lang="scss" scoped>
.form-style {
  .cover-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;

    :deep(.cover) {
      margin-right: 10px;
    }

    :deep(.image-upload) {
      margin-right: 10px;
    }
  }
}

.product-desc {
  width: 100%;
  height: calc(100vh - 400px);

  &.is-mobile {
    height: 300px;
    border-radius: 8px;
    overflow: hidden;
  }
}
</style>
