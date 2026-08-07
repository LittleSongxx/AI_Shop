<template>
  <div class="cases-page">
    <header class="page-head">
      <div>
        <p class="eyebrow">售后服务</p>
        <h1>我的工单</h1>
      </div>
      <el-button :loading="loading" @click="loadCases">刷新</el-button>
    </header>

    <div class="cases-layout">
      <section class="case-list-panel">
        <div v-if="loading && !cases.length" class="loading-state">加载中…</div>
        <div v-else-if="!cases.length" class="empty-state">暂无工单记录</div>
        <button
          v-for="item in cases"
          :key="String(item.caseId || item.caseNo)"
          type="button"
          class="case-item"
          :class="{ active: selectedId === String(item.caseNo || item.caseId) }"
          @click="selectCase(item)"
        >
          <div class="case-item-head">
            <strong>{{ item.categoryLabel || item.category || '其他' }}</strong>
            <span class="status" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
          </div>
          <p>{{ item.description || '暂无描述' }}</p>
          <small>{{ item.caseNo || item.caseId }} · {{ formatTime(item.updatedAt || item.createdAt) }}</small>
        </button>
      </section>

      <section class="case-detail-panel">
        <div v-if="detailLoading" class="loading-state">加载工单详情…</div>
        <div v-else-if="!selectedCase" class="empty-state">选择一张工单查看详情</div>
        <template v-else>
          <header class="detail-head">
            <div>
              <p class="eyebrow">{{ selectedCase.categoryLabel || selectedCase.category || '售后工单' }}</p>
              <h2>{{ selectedCase.caseNo || selectedCase.caseId }}</h2>
            </div>
            <span class="status" :class="statusClass(selectedCase.status)">{{ statusText(selectedCase.status) }}</span>
          </header>
          <dl class="detail-grid">
            <div><dt>关联订单</dt><dd>{{ selectedCase.orderId || '未关联' }}</dd></div>
            <div><dt>更新时间</dt><dd>{{ formatTime(selectedCase.updatedAt || selectedCase.createdAt) }}</dd></div>
            <div><dt>处理客服</dt><dd>{{ selectedCase.assignedAdmin || '待分配' }}</dd></div>
            <div><dt>人工会话</dt><dd>{{ selectedCase.supportSessionId || '未转人工' }}</dd></div>
          </dl>
          <section class="description-block">
            <h3>问题描述</h3>
            <p>{{ selectedCase.description || '暂无描述' }}</p>
          </section>
          <section v-if="evidencePath || selectedCase.evidence?.vlmDescription" class="evidence-block">
            <h3>图片证据</h3>
            <div class="evidence-content">
              <img v-if="evidencePath" :src="evidencePath" alt="售后图片证据" />
              <div>
                <p>审核状态：{{ selectedCase.evidence?.moderationStatus || '已记录' }}</p>
                <p v-if="selectedCase.evidence?.vlmStatus">图片分析：{{ selectedCase.evidence.vlmStatus }}</p>
                <p v-if="selectedCase.evidence?.vlmDescription">描述：{{ selectedCase.evidence.vlmDescription }}</p>
              </div>
            </div>
          </section>
          <section v-if="selectedCase.status === 'RESOLVED'" class="resolution-block">
            <h3>处理结果</h3>
            <p><b>{{ selectedCase.resolutionCode || '已处理' }}</b></p>
            <p v-if="selectedCase.rootCause">根因：{{ selectedCase.rootCause }}</p>
            <p v-if="selectedCase.resolutionSummary">说明：{{ selectedCase.resolutionSummary }}</p>
          </section>
        </template>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { agentApi, type SupportCase } from '@/api/modules';
import { resolveImageUrl } from '@/utils/image';
import { toast } from '@/utils/toast';

const route = useRoute();
const router = useRouter();
const cases = ref<SupportCase[]>([]);
const selectedCase = ref<SupportCase | null>(null);
const selectedId = ref('');
const loading = ref(false);
const detailLoading = ref(false);

const statusText = (status: unknown) => ({ OPEN: '待处理', IN_PROGRESS: '处理中', RESOLVED: '已解决', CANCELLED: '已取消' }[String(status || '').toUpperCase()] || '待处理');
const statusClass = (status: unknown) => `status-${String(status || 'OPEN').toLowerCase()}`;
const formatTime = (value: unknown) => value ? String(value).replace('T', ' ').slice(0, 19) : '—';
const evidencePath = computed(() => {
  const path = selectedCase.value?.evidence?.path;
  return path ? resolveImageUrl(path, { useThumbnail: true }) : '';
});

const selectCase = async (item: SupportCase) => {
  const id = String(item.caseNo || item.caseId);
  selectedId.value = id;
  await router.replace({ query: { ...route.query, caseId: id } });
  detailLoading.value = true;
  try {
    selectedCase.value = await agentApi.getSupportCase(id);
  } catch (error: any) {
    selectedCase.value = item;
    toast.error(error?.info || '工单详情加载失败');
  } finally {
    detailLoading.value = false;
  }
};

const loadCases = async () => {
  loading.value = true;
  try {
    cases.value = (await agentApi.listSupportCases(50)) || [];
    const queryId = String(route.query.caseId || '');
    const target = cases.value.find((item) => String(item.caseNo || item.caseId) === queryId) || cases.value[0];
    if (target) await selectCase(target);
    else { selectedCase.value = null; selectedId.value = ''; }
  } catch (error: any) {
    toast.error(error?.info || '工单加载失败，请稍后重试');
  } finally {
    loading.value = false;
  }
};

watch(() => route.query.caseId, () => {
  const id = String(route.query.caseId || '');
  const target = cases.value.find((item) => String(item.caseNo || item.caseId) === id);
  if (target && selectedId.value !== id) void selectCase(target);
});

onMounted(loadCases);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.cases-page { width: min(100%, 1080px); margin: 0 auto; padding: 16px $app-page-gutter 32px; box-sizing: border-box; }
.page-head, .detail-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-head { margin-bottom: 14px; }
.eyebrow { margin: 0; color: $color-primary; font-size: 12px; }
h1 { margin: 4px 0 0; color: $color-text-title; font-size: 23px; }
h2 { margin: 4px 0 0; color: $color-text-title; font-size: 17px; }
.cases-layout { display: grid; grid-template-columns: minmax(260px, 0.8fr) minmax(0, 1.5fr); gap: 12px; align-items: start; }
.case-list-panel, .case-detail-panel { min-width: 0; border: 1px solid $color-border; border-radius: $radius-card; background: $color-card; }
.case-list-panel { max-height: 680px; overflow-y: auto; }
.case-item { display: block; width: 100%; padding: 13px; border: 0; border-bottom: 1px solid $color-border-light; background: transparent; color: $color-text-body; text-align: left; cursor: pointer; }
.case-item:last-child { border-bottom: 0; }
.case-item.active { background: $color-primary-soft; box-shadow: inset 3px 0 0 $color-primary; }
.case-item-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.case-item strong { color: $color-text-title; font-size: 13px; }
.case-item p { margin: 7px 0; overflow: hidden; font-size: 12px; line-height: 1.45; text-overflow: ellipsis; white-space: nowrap; }
.case-item small { color: $color-text-muted; font-size: 10px; }
.status { display: inline-flex; align-items: center; min-height: 21px; padding: 0 7px; border-radius: $radius-pill; font-size: 11px; white-space: nowrap; }
.status-open { color: $color-warning; background: $color-warning-soft; }
.status-in_progress { color: $color-info; background: $color-info-soft; }
.status-resolved { color: $color-success; background: $color-success-soft; }
.status-cancelled { color: $color-text-muted; background: $color-surface-inset; }
.case-detail-panel { min-height: 370px; padding: 18px; box-sizing: border-box; }
.detail-head { padding-bottom: 14px; border-bottom: 1px solid $color-border-light; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }
.detail-grid div { min-width: 0; }
dt { color: $color-text-muted; font-size: 11px; }
dd { margin: 4px 0 0; overflow-wrap: anywhere; color: $color-text-body; font-size: 12px; }
.description-block, .evidence-block, .resolution-block { margin-top: 16px; padding-top: 14px; border-top: 1px solid $color-border-light; }
h3 { margin: 0 0 8px; color: $color-text-title; font-size: 13px; }
.description-block p, .resolution-block p, .evidence-content p { margin: 4px 0; color: $color-text-body; font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
.evidence-content { display: flex; align-items: flex-start; gap: 12px; }
.evidence-content img { width: 96px; height: 96px; flex: 0 0 auto; border-radius: $radius-sm; object-fit: cover; }
.loading-state, .empty-state { padding: 52px 16px; color: $color-text-muted; text-align: center; font-size: 13px; }
@media (max-width: 720px) {
  .cases-page { padding: 8px $app-page-gutter 24px; }
  .cases-layout { grid-template-columns: 1fr; }
  .case-list-panel { max-height: none; }
  .case-detail-panel { min-height: 300px; }
}
</style>
