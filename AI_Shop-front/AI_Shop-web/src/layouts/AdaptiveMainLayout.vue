<template>
  <component :is="layoutComponent" :key="platform" />
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useDevice } from '@/composables/useDevice';
import MainLayout from '@/layouts/MainLayout.vue';
import PcMainLayout from '@/layouts/PcMainLayout.vue';

const { isMobile, platform, sync } = useDevice();

const layoutComponent = computed(() => (isMobile.value ? MainLayout : PcMainLayout));

onMounted(() => {
  sync();
});
</script>
