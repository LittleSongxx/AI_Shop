<template>
  <div class="agent-page ignore">
    <AgentChatList />
    <AgentSendPanel />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useAgentSession } from '@/composables/useAgentSession';
import { useDevice } from '@/composables/useDevice';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { recoverIosViewportZoom, syncVisualViewportHeight } from '@/utils/mobileViewport';
import AgentChatList from '@/views/agent/AgentChatList.vue';
import AgentSendPanel from '@/views/agent/AgentSendPanel.vue';

const { isDesktop } = useDevice();
const { openAgent } = useOpenAgent();
const { start, stop } = useAgentSession();

onMounted(async () => {
  if (isDesktop.value) {
    openAgent();
    return;
  }
  document.body.classList.add('ios-agent-immersive');
  syncVisualViewportHeight();
  await start();
});

onUnmounted(() => {
  if (isDesktop.value) return;
  document.body.classList.remove('ios-agent-immersive');
  recoverIosViewportZoom();
  stop();
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.agent-page {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: transparent;
  box-sizing: border-box;

  > .chat-scroll {
    flex: 1 1 0;
    min-height: 0;
  }

  :deep(.bubble),
  :deep(.markdown-content),
  :deep(.stream-text),
  :deep(.typing),
  :deep(.cancel-tip),
  :deep(.biz-title),
  :deep(.user-bubble .text),
  :deep(.welcome),
  :deep(.tip-chip),
  :deep(.tips-label),
  :deep(.float-label) {
    font-size: 13px;
  }

  :deep(.markdown-content table) {
    font-size: 12px;
  }

  :deep(.agent-orders) {
    font-size: 12px;
  }
}
</style>
