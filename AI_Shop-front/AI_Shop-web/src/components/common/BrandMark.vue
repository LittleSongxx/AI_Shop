<template>
  <svg
    class="brand-mark"
    :class="rootClass"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 32 32"
    role="img"
    aria-label="智选 SmartSelect"
  >
    <rect x="2" y="2" width="28" height="28" rx="7" :fill="tileColor" />
    <path
      fill="none"
      :stroke="checkColor"
      stroke-width="2.7"
      stroke-linecap="round"
      stroke-linejoin="round"
      d="m8.8 16.2 4.7 4.7 9.7-10.1"
    />
    <path
      fill="none"
      :stroke="sparkColor"
      stroke-width="1.8"
      stroke-linecap="round"
      d="M9.4 7.1v4.2M7.3 9.2h4.2"
    />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    rootClass?: string;
    variant?: 'default' | 'light';
  }>(),
  { variant: 'default' }
);

const tileColor = computed(() => (props.variant === 'light' ? '#ffffff' : '#0f766e'));
const checkColor = computed(() => (props.variant === 'light' ? '#0f766e' : '#ffffff'));
const sparkColor = computed(() => (props.variant === 'light' ? '#2563eb' : '#bfdbfe'));
</script>

<style scoped lang="scss">
.brand-mark {
  display: block;
  flex-shrink: 0;
}
</style>
