<template>
  <Teleport to="body">
    <Transition name="cropper-fade">
      <div v-if="visible" class="avatar-cropper-overlay" @click.self="cancel">
        <div class="avatar-cropper-panel">
          <p class="cropper-title">移动和缩放图片</p>
          <div class="cropper-viewport">
            <Cropper
              ref="cropperRef"
              class="cropper-area"
              :src="imgSrc"
              :stencil-props="{ aspectRatio: 1, movable: false, resizable: false }"
              :stencil-component="CircleStencil"
              image-restriction="stencil"
              :min-zoom="0.3"
              :max-zoom="3"
              background-class="cropper-bg"
            />
          </div>
          <p class="cropper-hint">拖动图片调整位置，双指或滚轮缩放</p>
          <div class="cropper-actions">
            <el-button round @click="cancel">取消</el-button>
            <el-button type="primary" round :loading="confirming" @click="onConfirm">确定</el-button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, shallowRef } from 'vue';
import { Cropper, CircleStencil } from 'vue-advanced-cropper';
import 'vue-advanced-cropper/dist/style.css';

const visible = ref(false);
const imgSrc = ref('');
const confirming = ref(false);
const cropperRef = shallowRef<InstanceType<typeof Cropper> | null>(null);

let _resolve: ((blob: Blob) => void) | null = null;
let _reject: (() => void) | null = null;
let settled = false;

const open = (file: File): Promise<Blob> => {
  return new Promise((resolve, reject) => {
    settled = false;
    _resolve = resolve;
    _reject = reject;
    const reader = new FileReader();
    reader.onload = (e) => {
      const result = e.target?.result as string;
      if (!result || result.length < 100) {
        settled = true;
        reject(new Error('文件读取不完整'));
        return;
      }
      imgSrc.value = result;
      visible.value = true;
    };
    reader.onerror = () => {
      settled = true;
      reject(new Error('读取文件失败'));
    };
    reader.onabort = () => {
      settled = true;
      reject(new Error('文件读取被中断'));
    };
    reader.readAsDataURL(file);
  });
};

const onConfirm = () => {
  if (!cropperRef.value || settled) return;
  confirming.value = true;
  try {
    const result = cropperRef.value.getResult();
    const srcCanvas = result?.canvas;
    if (!srcCanvas || srcCanvas.width === 0 || srcCanvas.height === 0) {
      settled = true;
      visible.value = false;
      _reject?.();
      cleanup();
      return;
    }

    const SIZE = 256;
    const outCanvas = document.createElement('canvas');
    outCanvas.width = SIZE;
    outCanvas.height = SIZE;
    const ctx = outCanvas.getContext('2d');
    if (!ctx) {
      settled = true;
      visible.value = false;
      _reject?.();
      cleanup();
      return;
    }
    ctx.drawImage(srcCanvas, 0, 0, SIZE, SIZE);

    try {

      const dataUrl = outCanvas.toDataURL('image/png');
      const parts = dataUrl.split(',');
      if (parts.length !== 2 || !parts[1]) {
        throw new Error('dataUrl 为空');
      }
      const byteString = atob(parts[1]);
      if (!byteString || byteString.length === 0) {
        throw new Error('解码后数据为空');
      }
      const ab = new ArrayBuffer(byteString.length);
      const ia = new Uint8Array(ab);
      for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
      }
      const blob = new Blob([ab], { type: 'image/png' });
      if (blob.size === 0) {
        throw new Error('生成的 blob 为空');
      }
      settled = true;
      visible.value = false;
      _resolve?.(blob);
    } catch (e) {
      settled = true;
      visible.value = false;
      _reject?.();
      cleanup();
    }
  } finally {
    confirming.value = false;
  }
};

const cancel = () => {
  if (settled) return;
  settled = true;
  visible.value = false;
  _reject?.();
  cleanup();
};

const cleanup = () => {
  imgSrc.value = '';
  _resolve = null;
  _reject = null;
};

defineExpose({ open });
</script>

<style lang="scss">
.avatar-cropper-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
}

.avatar-cropper-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: min(92vw, 360px);
  padding: 24px 20px 20px;
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3);
}

.cropper-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1d1d1f;
}

.cropper-viewport {
  width: 280px;
  height: 280px;
  border-radius: 16px;
  overflow: hidden;
  background: #1a1a1a;
}

.cropper-area {
  width: 100%;
  height: 100%;
}

.cropper-bg {
  background: #1a1a1a;
}

.cropper-hint {
  margin: 0;
  font-size: 12px;
  color: #86868b;
}

.cropper-actions {
  display: flex;
  gap: 12px;
  width: 100%;

  .el-button {
    flex: 1;
  }
}

.cropper-fade-enter-active,
.cropper-fade-leave-active {
  transition: opacity 0.25s ease;
}
.cropper-fade-enter-from,
.cropper-fade-leave-to {
  opacity: 0;
}
</style>
