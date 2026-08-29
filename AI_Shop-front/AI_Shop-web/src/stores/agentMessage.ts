import { ref } from 'vue';
import { defineStore } from 'pinia';
import type { AgentSourceRef } from '@/utils/agentHistory';

export interface AgentWsMessage {
  messageId?: number | string;
  userMessage?: string;
  imageAssetId?: string;
  imageSnapshot?: Record<string, unknown>;
  selectedVisualSubject?: Record<string, unknown>;
  assistantMessage?: string;
  bizType?: string;
  outPutType?: number;
  userId?: string;
  messageType?: string;
  sourceRefs?: AgentSourceRef[] | { sources?: AgentSourceRef[] };
  schemaVersion?: number | string;
  runId?: string;
  requestId?: string;
  episodeId?: string;
  eventId?: string;
  seq?: number | string;
  terminalState?: string;
  replayCursor?: string;
}

export const useAgentMessageStore = defineStore('agentMessage', () => {
  const message = ref<AgentWsMessage | null>(null);

  const onMessage = (payload: AgentWsMessage) => {
    message.value = payload;
  };

  const clearMessage = () => {
    message.value = null;
  };

  return { message, onMessage, clearMessage };
});
