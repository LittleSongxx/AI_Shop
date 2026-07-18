<template>
  <div class="captcha-field ignore">
    <el-input
      v-model="innerValue"
      class="captcha-input-el"
      :placeholder="placeholder"
      maxlength="6"
      @input="$emit('update:modelValue', innerValue)"
    />
    <button
      type="button"
      class="captcha-preview"
      :class="{ loading: !captchaImage }"
      :title="captchaImage ? '点击刷新验证码' : '点击加载验证码'"
      @click="$emit('refresh')"
    >
      <img v-if="captchaImage" :src="captchaImage" class="captcha-img" alt="图形验证码" />
      <div v-else class="captcha-skeleton">
        <span class="dot" /><span class="dot" /><span class="dot" />
      </div>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';

const props = defineProps<{ modelValue: string; captchaImage: string; placeholder?: string }>();
defineEmits<{ 'update:modelValue': [string]; refresh: [] }>();
const innerValue = ref(props.modelValue);
watch(
  () => props.modelValue,
  (v) => (innerValue.value = v)
);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.captcha-field {
  display: flex;
  gap: 10px;
  width: 100%;
}

.captcha-input-el {
  flex: 1;
  min-width: 0;
}

.captcha-preview {
  position: relative;
  flex-shrink: 0;
  width: 118px;
  height: 44px;
  padding: 0;
  border: 1px solid $color-border-light;
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  background: linear-gradient(145deg, #fff, $color-bg-subtle);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;

  &:hover {
    border-color: rgba($color-gold, 0.45);
    box-shadow: 0 4px 14px rgba($color-gold, 0.12);
  }

  &:active {
    transform: scale(0.98);
  }

  &.loading {
    cursor: wait;
  }
}

.captcha-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  user-select: none;
}

.captcha-skeleton {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: linear-gradient(110deg, #f0f0f2 8%, #fafafa 18%, #f0f0f2 33%);
  background-size: 200% 100%;
  animation: shimmer 1.2s ease infinite;

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: $color-silver;
    opacity: 0.7;
  }
}

@keyframes shimmer {
  to {
    background-position-x: -200%;
  }
}
</style>
