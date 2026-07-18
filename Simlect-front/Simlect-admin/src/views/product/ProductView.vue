<template>
  <Drawer :show="dialogConfig.show" :title="dialogConfig.title" :buttons="dialogConfig.buttons" width="1300px"
    :showCancel="false" @close="dialogConfig.show = false">
    <div class="product-detail-view">
      <div class="product-image-panel">
        <div class="image-list">
          <div :class="['image-item', { active: image == selectedImage }]"
            v-for="image in productInfo?.cover?.split(',')">
            <Cover :source="image" :width="100" @click="selectImage(image)"></Cover>
          </div>
        </div>
        <div class="image-view">
          <Vue3ImageMagnifier :src="showImage" :zoom-src="showImage" width="100%" :zoom-width="500" :zoom-scale="2" />
        </div>
      </div>
      <div class="product-info-panel">
        <div class="product-name">{{ productInfo.productName }}</div>
        <div class="price-panel">
          <Price :price="selectedSku.price" :size="26"></Price>
        </div>
        <div class="property-list">
          <div class="property-item" v-for="property in productPropertyList">
            <div class="property-name">{{ property.propertyName }}</div>
            <div class="property-values">
              <div
                :class="['property-value-panel', { active: selectedProperty[property.propertyId] == value.propertyValueId }]"
                v-for="value in property.propertyValues" @click="selectProperty(property, value)">
                <Cover v-if="value.propertyCover" :source="value.propertyCover" :width="25"></Cover>
                <div class="property-value">
                  {{ value.propertyValue }}
                  <template v-if="value.propertyRemark">({{ value.propertyRemark }})</template>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="stock-panel">
          <div class="stock-label">库存</div>
          <div class="stock-value">{{ selectedSku.stock }}</div>
          <div class="stock-tips" v-if="selectedSku.stock <= 5">库存紧张</div>
        </div>
      </div>
    </div>
  </Drawer>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

import Vue3ImageMagnifier from 'vue3-image-magnifier'
import 'vue3-image-magnifier/dist/vue3-image-magnifier.css'

const dialogConfig = ref({
  show: false,
  title: '商品预览',
})
const productInfo = ref({})
const productPropertyList = ref([])
const skuList = ref([])
const getProduct = async (productId) => {
  let result = await proxy.Request({
    url: proxy.Api.getProductInfo,
    params: {
      productId,
    },
  })
  if (!result) {
    return
  }
  productInfo.value = result.data.productInfo
  
  selectImage(productInfo.value.cover.split(',')[0])

  productPropertyList.value = result.data.productPropertyList
  skuList.value = result.data.skuList
  
  initDefaultPropertySelected()
}

const showImage = computed(() => {
  return (
    proxy.Api.sourcePath +
    selectedImage.value?.replace(proxy.imageThumbnailSuffix, '')
  )
})

const selectedImage = ref()
const selectImage = (img) => {
  selectedImage.value = img
}

const show = (productId) => {
  dialogConfig.value.show = true
  getProduct(productId)
}


const selectedSku = ref({})
const selectedProperty = ref({})
const propertyImageMap = ref({})
const initDefaultPropertySelected = () => {
  selectedSku.value = skuList.value[0]
  const propertyValueIdArray = selectedSku.value.propertyValueIds.split('-')
  
  let initSelect = null
  for (let [index, property] of productPropertyList.value.entries()) {
    selectedProperty.value[property.propertyId] = propertyValueIdArray[index]
    for (const prop of property.propertyValues) {
      if (prop.propertyCover) {
        propertyImageMap.value[prop.propertyValueId] = prop.propertyCover
        
        if (initSelect == null) {
          selectImage(prop.propertyCover)
        }
        initSelect = prop.propertyCover
      }
    }
  }
}


const selectProperty = (property, propertyValue) => {
  const tempSelectedProperty = { ...selectedProperty.value }
  tempSelectedProperty[property.propertyId] = propertyValue.propertyValueId
  
  const selectedPropertyValueIds = productPropertyList.value
    .map((prop) => tempSelectedProperty[prop.propertyId])
    .join('-')
  const matchedSku = skuList.value.find(
    (sku) => sku.propertyValueIds === selectedPropertyValueIds
  )
  if (!matchedSku) {
    proxy.Message.warning('sku不存在')
    return
  }
  if (matchedSku.stock === 0) {
    proxy.Message.warning('sku对应的库存为0')
    return
  }
  selectedProperty.value[property.propertyId] = propertyValue.propertyValueId
  selectedSku.value = matchedSku
  
  const image = propertyImageMap.value[propertyValue.propertyValueId]
  if (image) {
    selectImage(image)
  }
}
defineExpose({
  show,
})
</script>

<style lang="scss" scoped>
.product-detail-view {
  display: flex;

  .product-image-panel {
    width: 810px;
    height: 700px;
    max-height: calc(100vh);
    display: flex;

    .image-list {
      width: 100px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;

      .image-item {
        margin-bottom: 10px;
        border-radius: 5px;
        overflow: hidden;
        border: 1px solid #fff;
        transition: border-color 0.2s ease;

        &:hover {
          border: 1px solid var(--gold-border);
        }
      }

      .active {
        border: 1px solid var(--gold);
      }
    }

    .image-view {
      flex: 1;
      width: 0;
      background: var(--pink);
      margin-left: 10px;
      display: flex;
      align-items: center;

      :deep(.magnifier-container) {
        height: 100%;

        img {
          object-fit: contain;
        }
      }

    }
  }

  .product-info-panel {
    flex: 1;
    width: 0;
    border-radius: 5px;
    margin-left: 10px;
    padding: 10px;

    .product-name {
      font-size: 24px;
      font-weight: 700;
      color: #333;
      margin-bottom: 15px;
    }

    .price-panel {
      margin-top: 10px;
    }

    .property-list {
      .property-item {
        margin-top: 20px;

        .property-name {
          font-size: 16px;
          font-weight: 600;
          color: #333;
        }

        .property-values {
          display: flex;
          flex-wrap: wrap;
          margin-top: 5px;

          .property-value-panel {
            display: flex;
            align-items: center;
            border: 1px solid #ddd;
            background-color: #f8f8f8;
            border-radius: 6px;
            margin-right: 15px;
            padding: 5px;
            margin-bottom: 10px;

            :deep(.image-panel) {
              margin-right: 5px;
            }

            &:hover {
              border: 1px solid var(--gold-border);
              background: var(--gold-soft);
            }

            .property-value {
              cursor: pointer;
              transition: all 0.2s ease;
              font-size: 14px;
              color: #333;
              position: relative;
              padding: 3px;
            }
          }

          .active {
            border: 1px solid var(--gold);
            background: var(--gold-soft);
          }
        }
      }
    }

    .stock-panel {
      margin-top: 20px;
      display: flex;
      align-items: center;

      .stock-label {
        font-size: 16px;
        font-weight: 600;
        color: #333;
      }

      .stock-value {
        margin-left: 10px;
        display: flex;
        align-items: center;
        color: var(--red);
        font-size: 16px;
        font-weight: 600;
      }

      .stock-tips {
        margin-left: 5px;
        color: #ffa202;
      }
    }
  }
}
</style>
