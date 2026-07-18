<template>
  <component :is="layoutComponent" :key="platform" />
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useDevice } from '@/composables/useDevice';
import PcSubPageLayout from '@/layouts/PcSubPageLayout.vue';
import SubPageLayout from '@/layouts/SubPageLayout.vue';

const { isMobile, platform, sync } = useDevice();

const layoutComponent = computed(() => (isMobile.value ? SubPageLayout : PcSubPageLayout));

onMounted(() => {
  sync();
});
</script>
