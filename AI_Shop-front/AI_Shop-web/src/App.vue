<template>
  <el-config-provider :locale="zhCn" :size="elementSize">
    <div class="page-texture" />
    <RouterView />
    <ProductSkuSheet />
    <ImagePreviewHost />
    <PcAgentFloatingPanel v-if="isDesktop" />
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { RouterView } from 'vue-router';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import ProductSkuSheet from '@/components/business/ProductSkuSheet.vue';
import ImagePreviewHost from '@/components/common/ImagePreviewHost.vue';
import PcAgentFloatingPanel from '@/components/pc/PcAgentFloatingPanel.vue';
import { useDeviceStore } from './stores/device';
import { useAuthStore } from './stores/auth';
import { useAppWebSocket } from '@/composables/useAppWebSocket';

const deviceStore = useDeviceStore();

const elementSize = computed(() => (deviceStore.isDesktop ? 'small' : 'default'));
const isDesktop = computed(() => deviceStore.isDesktop);

useAuthStore().tryRestoreSession();
useAppWebSocket();

onMounted(() => {
  deviceStore.sync();
});
</script>
