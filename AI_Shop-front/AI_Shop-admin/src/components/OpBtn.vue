<template>
  <el-tooltip v-if="icon" effect="dark" :content="tooltipText" placement="top" :disabled="!tooltipText">
    <div
      class="btn-panel btn-panel--icon"
      :class="[`btn-panel--${type}`, { 'is-disabled': disabled }]"
      :aria-label="tips"
      :aria-disabled="disabled"
      role="button"
      :tabindex="disabled ? -1 : 0"
      @click="handleClick"
      @keydown.enter.prevent="handleClick"
    >
      <div :class="['iconfont', icon]" :style="{ color: TYPE_MAP[type] }"></div>
    </div>
  </el-tooltip>
  <div
    v-else
    class="btn-panel btn-panel--text"
    :class="[`btn-panel--${type}`, { 'is-disabled': disabled }]"
    role="button"
    :aria-disabled="disabled"
    :tabindex="disabled ? -1 : 0"
    @click="handleClick"
    @keydown.enter.prevent="handleClick"
  >
    <span class="btn-text">{{ tips }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  icon: {
    type: String,
  },
  type: {
    type: String,
    default: 'primary',
  },
  tips: {
    type: String,
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  disabledTips: {
    type: String,
    default: '',
  },
  fun: {
    type: [String, Function],
  },
})

const tooltipText = computed(() => (props.disabled && props.disabledTips ? props.disabledTips : props.tips))

const TYPE_MAP = {
  primary: 'var(--primary)',
  success: 'var(--green)',
  warning: '#d4a24e',
  danger: '#e56b5b',
  info: '#8b95a8',
}

const emit = defineEmits(['click'])
const handleClick = (event) => {
  if (props.disabled) return
  emit('click', event)
}
</script>

<style lang="scss" scoped>
.btn-panel {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.2s;
  user-select: none;

  &--icon {
    width: 30px;
    height: 30px;
    background: var(--primary-muted);
    border: 1px solid rgba(201, 117, 79, 0.22);

    &:hover {
      background: var(--primary-soft);
      border-color: rgba(201, 117, 79, 0.35);
    }

    .iconfont {
      font-size: 14px;
      line-height: 1;
    }
  }

  &--text {
    min-width: 52px;
    height: 30px;
    padding: 0 10px;
    background: var(--primary-muted);
    border: 1px solid rgba(201, 117, 79, 0.22);

    &:hover {
      background: var(--primary-soft);
    }
  }

  &--danger.btn-panel--icon,
  &--danger.btn-panel--text {
    background: rgba(229, 107, 91, 0.1);
    border-color: rgba(229, 107, 91, 0.28);

    &:hover {
      background: rgba(229, 107, 91, 0.16);
    }
  }

  &--success.btn-panel--icon,
  &--success.btn-panel--text {
    background: rgba(20, 184, 166, 0.1);
    border-color: rgba(20, 184, 166, 0.28);

    &:hover {
      background: rgba(20, 184, 166, 0.16);
    }
  }

  &--warning.btn-panel--icon,
  &--warning.btn-panel--text {
    background: rgba(212, 162, 78, 0.12);
    border-color: rgba(212, 162, 78, 0.3);

    &:hover {
      background: rgba(212, 162, 78, 0.18);
    }
  }

  &.is-disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
}

.btn-text {
  font-size: 12px;
  color: var(--primary);
  white-space: nowrap;
}

.btn-panel--danger .btn-text {
  color: #e56b5b;
}

.btn-panel--success .btn-text {
  color: var(--green);
}

.btn-panel--warning .btn-text {
  color: #d4a24e;
}
</style>
