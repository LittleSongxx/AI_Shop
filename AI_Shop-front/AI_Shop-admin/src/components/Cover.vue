<template>
  <div class="image-panel" ref="coverRef" :style="{
    'border-radius': borderRadius,
    width: width ? width + 'px' : '100%',
    'aspect-ratio': scale
  }">
    <el-image v-if="coverFile" :lazy="lazy" :src="coverFile" :fit="fit" @click="showViewerHandler">
      <template #placeholder>
        <div class="loading" :style="{ height: loadingHeight + 'px' }">
          <img :src="proxy.Utils.getLocalResource('loading.gif')" />
        </div>
      </template>
    </el-image>
    <div v-else class="cover-empty">
      <span class="iconfont icon-image-error"></span>
    </div>
    <Teleport to="body">
      <div
        v-if="showViewer"
        class="eshop-image-lightbox allow-pinch-zoom"
        @click="closeViewer"
        @wheel.prevent="onWheel"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      >
        <img
          :key="viewerUrl"
          :src="viewerUrl"
          class="eshop-image-lightbox__img allow-pinch-zoom"
          :style="imgStyle"
          alt=""
          draggable="false"
          @click.stop
          @touchstart="onTouchStart"
          @touchmove="onTouchMove"
          @touchend="onTouchEnd"
          @touchcancel="onTouchEnd"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
        />
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import {
  ref,
  getCurrentInstance,
  computed,
  onMounted,
  onUnmounted,
  watch,
} from 'vue'
import { useImageLightboxZoom } from '@/composables/useImageLightboxZoom.js'

const { proxy } = getCurrentInstance()

const props = defineProps({
  source: {
    type: [String, File],
  },
  width: {
    type: Number,
  },
  scale: {
    type: Number,
    default: 1,
  },
  fit: {
    type: String,
    default: 'cover',
  },
  preview: {
    type: Boolean,
    default: false,
  },
  borderRadius: {
    type: String,
    default: '5px',
  },
  lazy: {
    type: Boolean,
    default: true,
  },
  preImageList: {
    type: Array,
    default: [],
  },
})

const coverFile = ref()
const getCover = async () => {
  if (props.source == 'avatar.png') {
    coverFile.value = proxy.Utils.getLocalResource('avatar.png')
    return
  }
  if (typeof props.source == 'string') {
    const trimmed = props.source.trim()
    if (!trimmed) {
      coverFile.value = ''
      return
    }
    coverFile.value = proxy.Api.sourcePath + trimmed
  } else if (props.source instanceof File) {
    let img = new FileReader()
    img.readAsDataURL(props.source)
    img.onload = ({ target }) => {
      coverFile.value = target.result
    }
  }
}

watch(
  () => props.source,
  async (newSource) => {
    getCover(newSource)
  },
  { immediate: true }
)

const imageList = computed(() => {
  return props.preImageList.map((item) => {
    return proxy.Api.sourcePath + item.replace(proxy.imageThumbnailSuffix, '')
  })
})

const viewerIndex = ref(0)
const showViewer = ref(false)

const viewerUrl = computed(() => imageList.value[viewerIndex.value] ?? '')

const {
  imgStyle,
  resetTransform,
  onWheel,
  onTouchStart,
  onTouchMove,
  onTouchEnd,
  onMouseDown,
  onMouseMove,
  onMouseUp,
} = useImageLightboxZoom()

const closeViewer = () => {
  showViewer.value = false
  document.body.style.overflow = ''
  resetTransform()
}

watch(viewerUrl, () => resetTransform())

const showViewerPrev = () => {
  const n = imageList.value.length
  if (n <= 1) return
  viewerIndex.value = (viewerIndex.value - 1 + n) % n
}

const showViewerNext = () => {
  const n = imageList.value.length
  if (n <= 1) return
  viewerIndex.value = (viewerIndex.value + 1) % n
}

const onViewerKeydown = (e) => {
  if (!showViewer.value) return
  if (e.key === 'Escape') closeViewer()
  else if (e.key === 'ArrowLeft') showViewerPrev()
  else if (e.key === 'ArrowRight') showViewerNext()
}

const showViewerHandler = () => {
  if (props.preImageList.length == 0) {
    return
  }
  const idx = props.preImageList.findIndex((item) => item === props.source)
  viewerIndex.value = idx >= 0 ? idx : 0
  showViewer.value = true
  document.body.style.overflow = 'hidden'
}

const coverRef = ref()
const loadingHeight = ref()
onMounted(() => {
  loadingHeight.value = coverRef.value.clientWidth * props.scale
  window.addEventListener('keydown', onViewerKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onViewerKeydown)
  document.body.style.overflow = ''
})
</script>

<style lang="scss" scoped>
.image-panel {
  position: relative;
  overflow: hidden;
  cursor: pointer;
  background: #f8f8f8;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  width: 100%;

  :deep(.el-image) {
    width: 100%;
    height: 100%;
  }

  :deep(.is-loading) {
    display: none;
  }

  :deep(.el-image__wrapper) {
    position: relative;
    vertical-align: top;
    width: 100%;
    height: 100%;
    display: flex;
  }

  .icon-image-error {
    margin: 0px auto;
    font-size: 20px;
    color: #838383;
    height: 100%;
  }

  .loading {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;

    img {
      width: 20px;
    }
  }

  .cover-empty {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #838383;
    font-size: 20px;
  }
}
</style>
