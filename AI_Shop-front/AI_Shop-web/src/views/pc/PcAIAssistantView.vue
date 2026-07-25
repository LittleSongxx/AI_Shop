<template>
  <div class="pc-agent-page agent-page ignore">
    <AgentChatList />
    <AgentSendPanel />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import AgentChatList from '@/views/agent/AgentChatList.vue';
import AgentSendPanel from '@/views/agent/AgentSendPanel.vue';
import { useAgentSession } from '@/composables/useAgentSession';
import { useDevice } from '@/composables/useDevice';
import { useOpenAgent } from '@/composables/useOpenAgent';

const { isDesktop } = useDevice();
const { openAgent } = useOpenAgent();
const { start, stop } = useAgentSession();

onMounted(async () => {
  if (isDesktop.value) {
    openAgent();
    return;
  }
  await start();
});

onUnmounted(() => {
  if (!isDesktop.value) stop();
});
</script>

<style scoped lang="scss">

</style>
