<template>
  <svg
    class="brand-mark-svg"
    :class="rootClass"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="6 5 20 22"
    role="img"
    aria-label="简选 Simlect"
  >
    <path
      fill="none"
      :stroke="markColor"
      :stroke-width="strokeWidth"
      stroke-linecap="round"
      stroke-linejoin="round"
      d="M20.5 8.5c-3.2 0-5.2 1.8-5.2 4.6 0 3.4 5.2 3.2 5.2 7.2 0 2.6-2.2 4.4-5.4 4.4-2.2 0-4-.8-5.1-2.1"
    />
    <path
      fill="none"
      :stroke="markColor"
      :stroke-width="strokeWidth"
      stroke-linecap="round"
      d="M11.5 8.5v14.6"
    />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  rootClass: String,
  variant: {
    type: String,
    default: 'default',
  },
})

const markColor = computed(() => (props.variant === 'light' ? '#ffffff' : '#1d1d1f'))
const strokeWidth = 1.85
</script>

<style scoped>
.brand-mark-svg {
  display: block;
  flex-shrink: 0;
}
</style>
