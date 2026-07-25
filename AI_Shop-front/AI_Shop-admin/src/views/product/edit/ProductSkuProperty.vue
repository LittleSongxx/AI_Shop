<template>

  <div v-if="isMobileAdmin" class="m-sku-property">
    <div class="glass-card m-sku-block">
      <h3 class="block-title">SKU 属性</h3>
      <p class="block-desc">设置规格维度与属性值，系统将自动生成 SKU 组合</p>

      <div
        v-for="(property, pIndex) in productEditStore.productPropertyList"
        :key="property.propertyId || pIndex"
        class="glass-card m-dimension-card"
      >
        <div class="dim-head">
          <span class="dim-name">{{ property.propertyName }}</span>
          <div class="dim-actions">
            <button type="button" class="dim-icon-btn" title="添加属性值" @click="addPropertyValue(pIndex)">
              <span class="iconfont icon-add"></span>
            </button>
            <button
              v-if="productEditStore.productPropertyList.length > 1"
              type="button"
              class="dim-icon-btn danger"
              title="移除此维度"
              @click="removePropertyDimension(pIndex)"
            >
              <span class="iconfont icon-delete"></span>
            </button>
          </div>
        </div>

        <div
          v-for="(propItem, vIndex) in property.propertyValues"
          :key="vIndex"
          class="dim-value-card"
        >
          <div class="value-index">属性值 {{ vIndex + 1 }}</div>
          <div v-if="property.coverType === 1" class="value-row-cover">
            <ImageSelect v-model="propItem.propertyCover" :cutWidth="150" :width="48" :scale="1" />
          </div>
          <el-input v-model="propItem.propertyValue" placeholder="属性值" clearable class="value-input" />
          <el-input
            v-model="propItem.propertyRemark"
            placeholder="备注（可选）"
            clearable
            class="remark-input"
            style="margin-top: 8px"
          />
          <button
            v-if="property.propertyValues.length > 1"
            type="button"
            class="op-btn sm danger block"
            style="margin-top: 8px"
            @click="removePropertyValue(pIndex, vIndex)"
          >
            删除此属性值
          </button>
        </div>
      </div>

      <div v-if="availableDimensions.length" class="m-add-dim">
        <span class="add-dim-label">已移除的 SKU 维度，点击恢复</span>
        <div class="add-dim-tags">
          <button
            v-for="item in availableDimensions"
            :key="item.propertyId"
            type="button"
            class="add-dim-tag"
            @click="restorePropertyDimension(item)"
          >
            + {{ item.propertyName }}
          </button>
        </div>
      </div>
    </div>
    <ProductSkuBuild ref="productSkuBuildRef"></ProductSkuBuild>
  </div>

  <div v-else class="sku-properties">
    <div v-for="(property, pIndex) in productEditStore.productPropertyList" :key="property.propertyId || pIndex">
      <div class="sku-name-panel">
        <div class="sku-name">{{ property.propertyName }}</div>
        <div class="sku-name-actions">
          <div class="iconfont icon-add" title="添加属性值" @click="addPropertyValue(pIndex)"></div>
          <div
            v-if="productEditStore.productPropertyList.length > 1"
            class="iconfont icon-delete"
            title="移除此 SKU 维度"
            @click="removePropertyDimension(pIndex)"
          ></div>
        </div>
      </div>
      <div class="sku-values">
        <div v-for="(propItem, vIndex) in property.propertyValues" :key="vIndex" class="sku-value-row">
          <div class="number">{{ vIndex + 1 }}.</div>
          <div class="cover" v-if="property.coverType === 1">
            <ImageSelect v-model="propItem.propertyCover" :cutWidth="150" :width="30" :scale="1"></ImageSelect>
          </div>
          <el-input v-model="propItem.propertyValue" placeholder="属性值" class="value-input" clearable></el-input>
          <el-input v-model="propItem.propertyRemark" placeholder="备注（可选）" class="remark-input" clearable></el-input>
          <div class="sku-op-panel">
            <div
              class="iconfont icon-delete"
              @click="removePropertyValue(pIndex, vIndex)"
              v-if="property.propertyValues.length > 1"
            ></div>
          </div>
        </div>
      </div>
      <el-divider v-if="pIndex < productEditStore.productPropertyList.length - 1" />
    </div>

    <div v-if="availableDimensions.length" class="add-dimension-panel">
      <span class="add-dimension-label">已移除的 SKU 维度：</span>
      <el-tag
        v-for="item in availableDimensions"
        :key="item.propertyId"
        class="add-dimension-tag"
        effect="plain"
        @click="restorePropertyDimension(item)"
      >
        + {{ item.propertyName }}
      </el-tag>
    </div>
    <ProductSkuBuild ref="productSkuBuildRef"></ProductSkuBuild>
  </div>
</template>

<script setup>
import ProductSkuBuild from './ProductSkuBuild.vue'
import ImageSelect from '@/components/ImageSelect.vue'
import { ref, computed, getCurrentInstance, watch } from 'vue'
const { proxy } = getCurrentInstance()
import { useRoute } from 'vue-router'
const route = useRoute()
import { useProductEditStore } from '@/stores/productEditStore'
const productEditStore = useProductEditStore()

const isMobileAdmin = computed(() => route.path.startsWith('/m/'))

const availableDimensions = computed(() => {
  const activeIds = new Set(productEditStore.productPropertyList.map((item) => item.propertyId))
  return productEditStore.categoryPropertyTemplates.filter((item) => !activeIds.has(item.propertyId))
})

const createDefaultPropertyValues = (index = 0) => [
  {
    propertyValueId: `${Date.now()}${index}`,
    propertyCover: '',
    propertyValue: '',
    propertyRemark: '',
  },
]

const addPropertyValue = (propertyIndex) => {
  productEditStore.productPropertyList[propertyIndex].propertyValues.push({
    propertyValueId: `${Date.now()}`,
    propertyCover: '',
    propertyValue: '',
    propertyRemark: '',
  })
}

const removePropertyValue = (propertyIndex, valueIndex) => {
  if (productEditStore.productPropertyList[propertyIndex].propertyValues.length > 1) {
    productEditStore.productPropertyList[propertyIndex].propertyValues.splice(valueIndex, 1)
  } else {
    proxy.Message.warning('至少需要保留一个属性值')
  }
}

const resetSkuGenerationState = () => {
  productEditStore.skuData = new Map()
  productEditStore.excludedSkuHashes = new Set()
}

const removePropertyDimension = (propertyIndex) => {
  if (productEditStore.productPropertyList.length <= 1) {
    proxy.Message.warning('至少保留一个 SKU 维度')
    return
  }
  const property = productEditStore.productPropertyList[propertyIndex]
  proxy.Confirm({
    message: `确定移除「${property.propertyName}」维度吗？将按剩余维度重新生成 SKU 列表`,
    okfun: () => {
      productEditStore.productPropertyList.splice(propertyIndex, 1)
      resetSkuGenerationState()
    },
  })
}

const restorePropertyDimension = (template) => {
  const restored = {
    ...template,
    propertyValues: createDefaultPropertyValues(),
  }
  const orderIds = productEditStore.categoryPropertyTemplates.map((item) => item.propertyId)
  const nextList = [...productEditStore.productPropertyList, restored]
  nextList.sort((a, b) => orderIds.indexOf(a.propertyId) - orderIds.indexOf(b.propertyId))
  productEditStore.productPropertyList = nextList
  resetSkuGenerationState()
}

const productSkuBuildRef = ref()
watch(
  () => productEditStore.productPropertyList,
  () => {
    productSkuBuildRef.value?.generateSkuList()
  },
  { deep: true }
)
</script>

<style lang="scss" scoped>
.sku-properties {
  height: calc(100%);
  overflow: auto;
  padding-right: 10px;
  margin-right: 10px;
  width: 450px;
  max-width: 100%;
  flex-shrink: 0;

  .sku-name-panel {
    display: flex;
    margin-bottom: 5px;
    justify-content: space-between;
    align-items: center;

    .sku-name {
      font-weight: bold;
    }

    .sku-name-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .icon-add,
    .icon-delete {
      cursor: pointer;
      border: 1px solid var(--header-border);
      background: var(--primary-soft);
      color: var(--text2);
      display: flex;
      height: 25px;
      width: 25px;
      align-items: center;
      justify-content: center;
      border-radius: 5px;
      transition: all 0.2s ease;

      &:hover {
        border-color: var(--gold-border);
        background: var(--gold-soft);
        color: var(--primary);
      }
    }
  }

  .sku-values {
    display: flex;
    flex-direction: column;

    .sku-value-row {
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 10px;

      .number {
        font-size: 14px;
        margin-right: 3px;
        color: #555555;
      }

      .cover {
        margin-right: 5px;
      }

      .value-input {
        flex: 1;
        margin-right: 5px;
      }

      .remark-input {
        width: 120px;
      }

      .sku-op-panel {
        margin-left: 10px;
        width: 20px;
        display: flex;
        justify-content: space-between;

        .iconfont {
          cursor: pointer;
        }
      }
    }
  }

  .add-dimension-panel {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed var(--header-border);

    .add-dimension-label {
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--text3);
    }

    .add-dimension-tag {
      margin: 0 8px 8px 0;
      cursor: pointer;
    }
  }
}

.m-sku-property {
  .value-input,
  .remark-input {
    width: 100%;
  }
}
</style>
