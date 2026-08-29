<template>
  <div
    ref="composerRef"
    class="agent-composer-stack ignore"
    :class="{ 'is-embedded': composerEmbedded }"
  >
    <section v-if="pendingProduct" class="consult-product-float">
      <div class="float-head">
        <p class="float-label">{{ resumeConsult ? '继续咨询商品' : '咨询商品' }}</p>
        <button
          type="button"
          class="float-close"
          aria-label="结束商品咨询"
          @click="dismissProductConsult"
        >
          <el-icon :size="14"><Close /></el-icon>
        </button>
      </div>
      <AgentConsultProductCard
        :product="pendingProduct"
        :resuming="resumeConsult"
        variant="composer"
        clickable
        @send="sendProductConsult"
      />
    </section>

    <LiquidGlassSurface intensity="medium" class="agent-composer-dock">
    <section class="quick-tips">
      <div class="tips-row">
        <span class="tips-label">试试这样说</span>
        <div class="tips-scroll-wrap">
          <button
            v-if="isDesktop && canScrollLeft"
            type="button"
            class="scroll-arrow scroll-left"
            aria-label="向左滚动"
            @click="scrollTips(-1)"
          >
            <el-icon :size="12"><ArrowLeft /></el-icon>
          </button>
          <div
            ref="tipsScrollRef"
            class="tips-scroll"
            role="list"
            aria-label="快捷提问"
            @scroll="onTipsScroll"
          >
            <button
              v-for="tip in tips"
              :key="tip"
              type="button"
              class="tip-chip"
              role="listitem"
              @click="applyTip(tip)"
            >
              {{ tip }}
            </button>
          </div>
          <button
            v-if="isDesktop && canScrollRight"
            type="button"
            class="scroll-arrow scroll-right"
            aria-label="向右滚动"
            @click="scrollTips(1)"
          >
            <el-icon :size="12"><ArrowRight /></el-icon>
          </button>
        </div>
      </div>
    </section>

    <footer class="chat-input-bar ignore">
      <input
        ref="imageInputRef"
        class="image-input"
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
        @change="onImageSelected"
      />
      <div v-if="imageAttachment" class="attachment-strip">
        <img :src="imageAttachment.previewUrl" alt="商品图片预览" class="attachment-preview" />
        <div class="attachment-meta">
          <span>{{ imageAttachment.statusText }}</span>
          <small v-if="imageAttachment.moderationStatus">审核：{{ imageAttachment.moderationStatus }}</small>
        </div>
        <button type="button" class="attachment-remove" aria-label="删除图片" @click="removeAttachment">删除</button>
      </div>
      <div class="input-leading">
        <button
          type="button"
          class="image-attach-btn"
          aria-label="添加商品图片"
          title="添加商品图片"
          :disabled="answering || uploadingImage"
          @click="openImagePicker"
        >
          {{ uploadingImage ? '上传中' : '图片' }}
        </button>
      </div>
      <textarea
        ref="textareaRef"
        v-model="input"
        class="agent-chat-textarea"
        rows="1"
        maxlength="500"
        enterkeyhint="send"
        autocomplete="off"
        autocorrect="on"
        placeholder="输入问题，或上传图片找同款"
        :disabled="answering"
        :readonly="!isDesktop"
        @focus="onTextareaFocus"
        @blur="onTextareaBlur"
        @input="onTextareaInput"
        @keydown="onTextareaKeydown"
      />
      <button
        type="button"
        class="btn-human btn-send-native"
        aria-label="转人工客服"
        :disabled="answering"
        @click="requestHumanSupport"
      >
        人工
      </button>
      <button
        v-if="!answering"
        type="button"
        class="btn-send btn-send-native"
        aria-label="发送消息"
        :disabled="!canSubmitMessage"
        @click="sendMessage"
      >
        发送
      </button>
      <button v-else type="button" class="btn-send btn-send-native btn-stop" aria-label="停止生成" @click="stop">停止</button>
    </footer>
    </LiquidGlassSurface>
  </div>
</template>

<script setup lang="ts">
import { inject, nextTick, onMounted, onUnmounted, ref, watch, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { agentComposerEmbeddedKey } from '@/composables/agentEmbed';
import { useDevice } from '@/composables/useDevice';
import { agentApi, fileApi, type ImageUploadResult } from '@/api/modules';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import AgentConsultProductCard from '@/components/agent/AgentConsultProductCard.vue';
import { AGENT_OUTPUT_TYPE } from '@/constants/backendEnums';
import { useAgentMessageStore } from '@/stores/agentMessage';
import { useAuthStore } from '@/stores/auth';
import { usePcAgentPanelStore } from '@/stores/pcAgentPanel';
import {
  buildProductConsultMessage,
  clearAgentConsultProduct,
  loadAgentConsultProduct,
  type AgentConsultProduct
} from '@/utils/agentProductConsult';
import { ensureAppWebSocket } from '@/utils/websocket/manager';
import { mitter } from '@/utils/eventBus';
import { lockViewportAfterInput, recoverIosViewportZoom } from '@/utils/mobileViewport';
import { toast } from '@/utils/toast';
import { showTopAlert } from '@/utils/topAlert';

const TIP_POOL = [
  '帮我推荐热销商品',
  '我的订单到哪了',
  '如何申请退款',
  '有什么优惠活动',
  '我要评价订单',
  '我的优惠券在哪用',
  '最近有什么新品',
  '如何修改收货地址',
  '怎么查看物流信息',
  '如何取消订单',
  '商品有质量问题怎么办',
  '如何查看我的足迹',
  '我的收藏在哪里看',
  '签到有什么奖励',
  '怎么查看会员等级',
  '支付方式有哪些',
  '上次买的商品能再买一次吗',
  '如何换货',
  '如何举报评价',
  '我的消息在哪里看'
];

const PRODUCT_CONSULT_TIPS = [
  '这件商品有货吗',
  '有哪些规格可选',
  '适合什么人用',
  '和同类比有什么优势',
  '退换货怎么算',
  '大概几天能到货'
];

function pickRandom<T>(arr: T[], count: number): T[] {
  const shuffled = [...arr];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, count);
}

const pendingProduct = ref<AgentConsultProduct | null>(null);
const resumeConsult = ref(false);

const tips = computed(() => {
  if (pendingProduct.value) {
    return PRODUCT_CONSULT_TIPS;
  }
  return pickRandom(TIP_POOL, 3);
});

const composerEmbedded = inject(agentComposerEmbeddedKey, false);

const { isDesktop } = useDevice();

const tipsScrollRef = ref<HTMLElement | null>(null);
const canScrollLeft = ref(false);
const canScrollRight = ref(false);

const checkScrollable = () => {
  const el = tipsScrollRef.value;
  if (!el) { canScrollLeft.value = false; canScrollRight.value = false; return; }
  canScrollLeft.value = el.scrollLeft > 2;
  canScrollRight.value = el.scrollLeft < el.scrollWidth - el.clientWidth - 2;
};

const onTipsScroll = () => { checkScrollable(); };

const scrollTips = (dir: number) => {
  const el = tipsScrollRef.value;
  if (!el) return;
  const step = Math.max(el.clientWidth * 0.6, 120);
  el.scrollBy({ left: dir * step, behavior: 'smooth' });

  setTimeout(checkScrollable, 350);
};

const agentMessageStore = useAgentMessageStore();
const authStore = useAuthStore();
const pcAgentPanel = usePcAgentPanelStore();
const route = useRoute();
const router = useRouter();

const currentUserId = () => authStore.userInfo?.userId as string | undefined;

const initPendingConsultProduct = () => {
  const userId = currentUserId();
  if (composerEmbedded) {
    if (pcAgentPanel.consumeFromProduct()) {
      pendingProduct.value = loadAgentConsultProduct(userId);
      return;
    }
    pendingProduct.value = null;
    return;
  }
  const product = loadAgentConsultProduct(userId) || loadAgentConsultProduct();
  if (product) {
    pendingProduct.value = product;
    clearAgentConsultProduct(userId);
    clearAgentConsultProduct();
  }
};

const syncConsultResumeState = async () => {
  resumeConsult.value = false;
  const product = pendingProduct.value;
  if (!product?.productId || !authStore.isLoggedIn) return;
  try {
    const ctx = await agentApi.getProductConsultContext();
    resumeConsult.value = ctx?.productId === product.productId;
  } catch {
    resumeConsult.value = false;
  }
};

const resolveConsultProductId = (): string | undefined => {
  if (pendingProduct.value?.productId) return pendingProduct.value.productId;
  const m = window.location.pathname.match(/^\/product\/([^/]+)$/);
  return m?.[1];
};

const composerRef = ref<HTMLElement | null>(null);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const imageInputRef = ref<HTMLInputElement | null>(null);
const input = ref('');
const TEXTAREA_MAX_HEIGHT = 96;
const answering = ref(false);
const messageId = ref<number | null>(null);

type ImageAttachment = ImageUploadResult & { previewUrl: string; statusText: string };
const imageAttachment = ref<ImageAttachment | null>(null);
const uploadingImage = ref(false);
const imageUploadError = ref('');
const comparisonProductIds = ref<string[]>([]);

const attachmentReady = computed(() =>
  !imageAttachment.value || (
    imageAttachment.value.moderationStatus === 'APPROVED' && !!imageAttachment.value.assetId
  )
);

const canSubmitMessage = computed(() => {
  if ((!input.value.trim() && !imageAttachment.value?.assetId) || answering.value || uploadingImage.value) return false;
  return attachmentReady.value;
});

const resetComposerIdle = () => {
  answering.value = false;
  messageId.value = null;
  if (isDesktop.value) {
    void nextTick(() => textareaRef.value?.focus());
  }
};

let composerResizeObserver: ResizeObserver | null = null;

let composerInsetReadyEmitted = false;

const syncComposerInset = () => {
  if (isDesktop.value) {
    document.documentElement.style.setProperty('--agent-composer-inset', '0px');
    if (!composerInsetReadyEmitted) {
      composerInsetReadyEmitted = true;
      mitter.emit('agentComposerReady');
    }
    return;
  }
  const height = composerRef.value?.offsetHeight ?? 0;
  document.documentElement.style.setProperty('--agent-composer-inset', `${height}px`);
  if (!composerInsetReadyEmitted && height > 0) {
    composerInsetReadyEmitted = true;
    mitter.emit('agentComposerReady');
  }
};

const teardownComposerInset = () => {
  composerResizeObserver?.disconnect();
  composerResizeObserver = null;
  document.documentElement.style.removeProperty('--agent-composer-inset');
};

const adjustTextareaHeight = () => {
  const el = textareaRef.value;
  if (!el) return;
  el.style.height = 'auto';
  const next = Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT);
  el.style.height = `${Math.max(next, 40)}px`;
};

const onTextareaFocus = (event: FocusEvent) => {
  const el = event.target as HTMLTextAreaElement;
  el.removeAttribute('readonly');
  adjustTextareaHeight();
};

const onTextareaBlur = () => {
  const el = textareaRef.value;
  if (el) el.setAttribute('readonly', '');
  recoverIosViewportZoom();
  window.setTimeout(lockViewportAfterInput, 300);
};

const onTextareaInput = () => {
  adjustTextareaHeight();
};

const onTextareaKeydown = (event: KeyboardEvent) => {
  if (!isDesktop.value) return;
  if (event.key !== 'Enter') return;
  if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  void sendMessage();
};

const applyTip = (tip: string) => {
  input.value = tip;
  adjustTextareaHeight();
};

const openImagePicker = () => {
  if (answering.value || uploadingImage.value) return;
  imageInputRef.value?.click();
};

const removeAttachment = () => {
  if (imageAttachment.value?.previewUrl.startsWith('blob:')) {
    URL.revokeObjectURL(imageAttachment.value.previewUrl);
  }
  imageAttachment.value = null;
  imageUploadError.value = '';
  if (imageInputRef.value) imageInputRef.value.value = '';
};

const onImageSelected = async (event: Event) => {
  const inputEl = event.target as HTMLInputElement;
  const file = inputEl.files?.[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    toast.error('请选择图片文件');
    inputEl.value = '';
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    toast.error('图片不能超过 10MB');
    inputEl.value = '';
    return;
  }
  removeAttachment();
  uploadingImage.value = true;
  imageUploadError.value = '';
  const localPreview = URL.createObjectURL(file);
  try {
    const uploaded = await fileApi.uploadImage(file, true, 'agent');
    const moderationStatus = String(uploaded.moderationStatus || (uploaded.pendingReview ? 'PENDING' : 'APPROVED')).toUpperCase();
    imageAttachment.value = {
      ...uploaded,
      moderationStatus,
      previewUrl: localPreview,
      statusText: moderationStatus === 'APPROVED' ? '图片已通过审核，可以发送' : '图片待审核，暂不能发送'
    };
    if (moderationStatus !== 'APPROVED') toast.warning('图片正在审核，通过后才能发送');
  } catch (error: any) {
    URL.revokeObjectURL(localPreview);
    imageUploadError.value = error?.info || error?.message || '图片上传失败';
    toast.error(imageUploadError.value);
  } finally {
    uploadingImage.value = false;
    inputEl.value = '';
  }
};

const onCompareProducts = (payload?: unknown) => {
  const ids = (payload as { productIds?: unknown })?.productIds;
  if (!Array.isArray(ids)) return;
  comparisonProductIds.value = [...new Set(ids.map((id) => String(id).trim()).filter(Boolean))].slice(0, 4);
  if (comparisonProductIds.value.length < 2) return;
  input.value = '请比较我选择的商品';
  adjustTextareaHeight();
  if (!answering.value) void sendMessage();
};

const dispatchSend = async (text: string, options?: { comparisonProductIds?: string[] }) => {
  const imageAssetId = imageAttachment.value?.assetId;
  if ((!text && !imageAssetId) || answering.value) return false;
  if (!attachmentReady.value) {
    toast.warning('请等待图片审核通过后再发送');
    return false;
  }

  try {
    // The HTTP enqueue response is not enough to render a live answer.  The
    // websocket manager resolves only after OPEN (or returns false after its
    // bounded retry budget), so we never create a request that cannot be
    // delivered to the current browser.
    const wsReady = await ensureAppWebSocket();
    if (wsReady === false) {
      toast.error('实时连接不可用，请稍后重试');
      return false;
    }
    const path = window.location.pathname;
    const isProductPage = /^\/product\/\d+$/.test(path);
    const fromProduct = pendingProduct.value !== null || isProductPage;
    const consultProductId = fromProduct ? resolveConsultProductId() : undefined;
    const data = await agentApi.sendMessage(text, fromProduct, consultProductId, {
      imageAssetId,
      comparisonProductIds: options?.comparisonProductIds || comparisonProductIds.value
    });
    if (!data?.messageId) {
      toast.error('发送失败，请重试');
      return false;
    }
    messageId.value = data.messageId;
    data.assistantMessage = '';
    answering.value = true;
    mitter.emit('sendMessage', { ...data });
    removeAttachment();
    comparisonProductIds.value = [];
    return true;
  } catch (e: any) {
    if (e?.info === 'AI购物体验已经结束') {
      showTopAlert('AI购物体验已经结束');
      answering.value = false;
      return false;
    }
    toast.error(e?.info || '发送失败，请重试');
    return false;
  }
};

const sendMessage = async () => {
  const text = input.value.trim();
  if ((!text && !imageAttachment.value?.assetId) || answering.value) return;
  const ok = await dispatchSend(text);
  if (ok) {
    input.value = '';
    adjustTextareaHeight();
    if (!isDesktop.value) {
      textareaRef.value?.blur();
      recoverIosViewportZoom();
    } else {
      textareaRef.value?.focus();
    }
  }
};

const sendProductConsult = async () => {
  const product = pendingProduct.value;
  if (!product || answering.value) return;
  const text = buildProductConsultMessage(product);
  const ok = await dispatchSend(text);
  if (ok) {
    pendingProduct.value = null;
    clearAgentConsultProduct(currentUserId());
  }
};

const requestHumanSupport = async () => {
  if (answering.value) return;
  const reason = input.value.trim();
  const text = reason ? `转人工客服。原因：${reason}` : '转人工客服';
  const ok = await dispatchSend(text);
  if (ok) {
    input.value = '';
    adjustTextareaHeight();
    toast.success('已提交人工客服请求');
  }
};

const dismissProductConsult = async () => {
  pendingProduct.value = null;
  clearAgentConsultProduct(currentUserId());
  clearAgentConsultProduct();
  try {
    await agentApi.clearProductConsult();
  } catch {

  }
};

const stop = () => {
  const id = messageId.value;
  if (id == null) {
    toast.error('无法取消，请刷新页面后重试');
    return;
  }

  mitter.emit('cancelMessage', { messageId: id });
};

const onAnswering = (val: unknown) => {
  if (typeof val === 'boolean') {
    if (val) {
      answering.value = true;
    } else {
      resetComposerIdle();
    }
    return;
  }
  const payload = val as { answering?: boolean; messageId?: number };
  if (payload.answering) {
    answering.value = true;
    if (payload.messageId != null) messageId.value = payload.messageId;
  } else {
    resetComposerIdle();
  }
};

watch(input, () => {
  adjustTextareaHeight();
});

watch(
  () => agentMessageStore.message,
  (msg) => {
    if (!msg) return;
    const outputType = Number(msg.outPutType);
    if (
      (outputType === AGENT_OUTPUT_TYPE.DONE || outputType === AGENT_OUTPUT_TYPE.ERROR) &&
      messageId.value != null &&
      msg.messageId != null &&
      String(messageId.value) === String(msg.messageId)
    ) {
      resetComposerIdle();
    }
  }
);

const onSendMessageSync = (payload: unknown) => {
  const msg = payload as { messageId?: number };
  if (msg?.messageId != null) messageId.value = msg.messageId;
};

onMounted(() => {
  initPendingConsultProduct();
  void syncConsultResumeState();

  const fromProductEntry = route.query.fromProduct === '1';
  if (!pendingProduct.value && !fromProductEntry && authStore.isLoggedIn) {
    void agentApi.pauseProductConsult().catch(() => {});
  }
  mitter.on('answering', onAnswering);
  mitter.on('sendMessage', onSendMessageSync);
  mitter.on('compareProducts', onCompareProducts);
  syncComposerInset();
  composerResizeObserver = new ResizeObserver(syncComposerInset);
  if (composerRef.value) composerResizeObserver.observe(composerRef.value);
  adjustTextareaHeight();

  nextTick(() => {
    checkScrollable();
    const scrollEl = tipsScrollRef.value;
    if (scrollEl) {
      const ro = new ResizeObserver(() => { checkScrollable(); });
      ro.observe(scrollEl);
    }
  });

  const presetMsg = route.query.msg as string | undefined;
  if (presetMsg) {
    router.replace({ query: { ...route.query, msg: undefined } });
    setTimeout(() => {
      if (!answering.value) {
        input.value = presetMsg;
        adjustTextareaHeight();
        sendMessage();
      }
    }, 400);
  }
});

onUnmounted(() => {
  recoverIosViewportZoom();
  mitter.off('answering', onAnswering);
  mitter.off('sendMessage', onSendMessageSync);
  mitter.off('compareProducts', onCompareProducts);
  removeAttachment();
  teardownComposerInset();
  composerInsetReadyEmitted = false;
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.agent-composer-stack {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: $z-index-agent-composer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  max-width: none;
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
  padding-bottom: env(safe-area-inset-bottom, 0);
  background: transparent;
  pointer-events: none;

  &.is-embedded {
    position: static;
    left: auto;
    right: auto;
    bottom: auto;
    z-index: 1;
    flex-shrink: 0;
    width: 100%;
    max-width: 100%;
    padding: 0;
    pointer-events: auto;
    gap: 0;
  }
}

.consult-product-float {
  flex-shrink: 0;
  margin: 0 $app-page-gutter;
  padding: 10px 12px;
  border-radius: $radius-card;
  background: var(--glass-bg-light);
  -webkit-backdrop-filter: var(--glass-blur-sm);
  backdrop-filter: var(--glass-blur-sm);
  border: 1px solid var(--glass-border-soft);
  box-shadow: var(--glass-shadow-sm);
  pointer-events: auto;

  .float-label {
    margin: 0;
    font-size: 12px;
    font-weight: 600;
    color: $color-text-muted;
  }
}

.float-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.float-close {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: $color-text-muted;
  cursor: pointer;

  &:hover {
    color: $color-text-title;
    background: $color-bg-subtle;
  }
}

.agent-composer-dock {
  flex-shrink: 0;
  border-top: 1px solid var(--glass-border-soft);
  box-shadow: 0 -6px 24px rgba(20, 22, 26, 0.06);
  pointer-events: auto;
  touch-action: manipulation;
}

.quick-tips {
  flex-shrink: 0;
  padding: 10px 0 10px $app-page-gutter;
}

.tips-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.tips-label {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: $color-text-muted;
  white-space: nowrap;
}

.tips-scroll-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding-right: $app-page-gutter;
}

.tips-scroll {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  scroll-snap-type: x proximity;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.scroll-arrow {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 1px solid $color-border-gray;
  border-radius: 50%;
  background: #fff;
  color: $color-text-muted;
  cursor: pointer;
  transition: color $transition-fast, border-color $transition-fast;
  padding: 0;

  &:hover {
    color: $color-text-title;
    border-color: $color-text-muted;
  }

  &:active {
    background: $color-bg-subtle;
  }
}

.tip-chip {
  flex-shrink: 0;
  scroll-snap-align: start;
  white-space: nowrap;
  border: 1px solid rgba($color-primary, 0.2);
  background: $color-surface-inset;
  color: $color-text-title;
  font-size: 12px;
  font-weight: 500;
  padding: 7px 12px;
  border-radius: $radius-pill;
  cursor: pointer;
  transition: background $transition-fast, border-color $transition-fast, color $transition-fast;
  -webkit-tap-highlight-color: transparent;

  &:active {
    background: $color-primary-soft;
    border-color: rgba($color-primary, 0.35);
    color: $color-primary;
  }
}

.chat-input-bar {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  padding: 10px $app-page-gutter;

  .image-input { display: none; }

  .attachment-strip {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-width: 0;
    padding: 6px 8px;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
    background: $color-surface-inset;
  }

  .attachment-preview {
    width: 40px;
    height: 40px;
    flex: 0 0 auto;
    border-radius: $radius-xs;
    object-fit: cover;
    background: #fff;
  }

  .attachment-meta {
    display: flex;
    flex: 1;
    min-width: 0;
    flex-direction: column;
    gap: 2px;
    color: $color-text-body;
    font-size: 12px;

    small { color: $color-text-muted; font-size: 10px; }
  }

  .attachment-remove,
  .image-attach-btn {
    min-height: 32px;
    padding: 0 9px;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
    background: #fff;
    color: $color-text-muted;
    font-size: 12px;
    cursor: pointer;

    &:disabled { cursor: not-allowed; opacity: 0.5; }
  }

  .attachment-remove { flex: 0 0 auto; color: $color-error; }
  .input-leading { flex: 0 0 auto; display: flex; align-items: flex-end; }

  .agent-chat-textarea {
    flex: 1;
    min-width: 0;
    min-height: 40px;
    max-height: 96px;
    margin: 0;
    padding: 9px 12px;
    border: 1px solid $color-border;
    border-radius: $radius-sm;
    font-size: 16px;
    line-height: 1.45;
    font-family: inherit;
    color: $color-text-title;
    background: #fff;
    resize: none;
    outline: none;
    box-sizing: border-box;
    touch-action: manipulation;
    -webkit-text-size-adjust: 100%;
    appearance: none;

    &::placeholder {
      color: $color-text-muted;
    }

    &:disabled {
      background: $color-surface-inset;
      color: $color-text-disabled;
    }
  }

  .btn-send-native {
    flex-shrink: 0;
    height: 40px;
    min-width: 72px;
    padding: 0 16px;
    border: none;
    border-radius: $radius-sm;
    font-size: 16px;
    font-weight: 600;
    color: #fff;
    background: $color-primary;
    cursor: pointer;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;

    &:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    &.btn-stop {
      color: $color-error;
      background: #fff;
      border: 1px solid $color-error-border;
    }

    &.btn-human {
      min-width: 60px;
      color: $color-primary;
      background: #fff;
      border: 1px solid rgba($color-primary, 0.32);
    }
  }
}
</style>
