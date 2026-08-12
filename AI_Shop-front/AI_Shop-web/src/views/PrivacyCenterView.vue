<template>
  <div class="privacy-page">
    <section class="scope-panel">
      <header>
        <div class="scope-icon"><el-icon><DataAnalysis /></el-icon></div>
        <div><h2>AI 数据生命周期</h2><p>导出和彻底删除均为异步任务，且只允许当前账号访问。</p></div>
      </header>
      <div class="scope-grid">
        <div><b>可导出或删除</b><span>AI 对话、摘要、购物画像、长期记忆、Agent Trace、客服 AI 数据和可删除归因记录。</span></div>
        <div><b>必须保留</b><span>订单、支付等法定或履约所需数据不直接删除，只解除 AI 关联并匿名化。</span></div>
      </div>
      <el-alert
        title="清空聊天不等于彻底删除 AI 数据"
        description="聊天页的清空操作只隐藏历史会话；本页删除任务会按步骤处理画像、记忆、Trace 和其他 AI 数据。"
        type="info"
        :closable="false"
        show-icon
      />
    </section>

    <section class="action-panel">
      <article>
        <div><h3>导出我的 AI 数据</h3><p>完成后生成仅当前账号可下载的短期 JSON 文件。</p></div>
        <el-button type="primary" plain :loading="creatingType === 'EXPORT'" @click="openConfirmation('EXPORT')">
          <el-icon><Download /></el-icon><span>申请导出</span>
        </el-button>
      </article>
      <article class="danger-action">
        <div><h3>彻底删除我的 AI 数据</h3><p>任务可失败重试；完成后不可通过聊天恢复个人画像和历史 Trace。</p></div>
        <el-button type="danger" plain :loading="creatingType === 'DELETE'" @click="openConfirmation('DELETE')">
          <el-icon><Delete /></el-icon><span>申请删除</span>
        </el-button>
      </article>
    </section>

    <section class="jobs-panel">
      <header class="jobs-head">
        <div><h2>处理记录</h2><p>进行中的任务每 5 秒自动刷新，也可手动刷新。</p></div>
        <el-button :icon="Refresh" circle title="刷新处理记录" :loading="loading" @click="loadJobs" />
      </header>
      <div v-if="jobs.length" class="job-list" v-loading="loading">
        <article v-for="job in jobs" :key="job.jobId" class="job-row">
          <div class="job-main">
            <div class="job-title-row">
              <b>{{ typeLabel(job.jobType) }}</b>
              <el-tag :type="statusTagType(job.status)" size="small">{{ statusLabel(job.status) }}</el-tag>
            </div>
            <span class="job-id">{{ job.jobId }}</span>
            <el-progress :percentage="job.progress?.percent || 0" :status="job.status === 'FAILED' ? 'exception' : job.status === 'COMPLETED' ? 'success' : undefined" />
            <p class="job-step">{{ stepLabel(job.currentStep) }} · {{ job.progress?.completed || 0 }} / {{ job.progress?.total || 0 }} 步</p>
            <p v-if="job.errorMessage" class="job-error">{{ job.errorMessage }}</p>
            <p v-if="job.downloadable && job.exportExpiresAt" class="job-expiry">下载有效期至 {{ formatTime(job.exportExpiresAt) }}</p>
          </div>
          <div class="job-actions">
            <el-button v-if="canDownload(job)" type="primary" :loading="downloadingId === job.jobId" @click="download(job)">下载</el-button>
            <el-button v-if="canRetry(job)" :loading="retryingId === job.jobId" @click="retry(job)">重试</el-button>
          </div>
        </article>
      </div>
      <el-empty v-else-if="!loading" description="暂无 AI 数据导出或删除任务" :image-size="76" />
    </section>

    <el-dialog v-model="confirmDialog.show" :title="confirmDialog.type === 'DELETE' ? '确认删除 AI 数据' : '确认导出 AI 数据'" width="min(440px, 92vw)" :close-on-click-modal="false">
      <p class="confirm-copy">
        {{ confirmDialog.type === 'DELETE' ? '删除任务会处理全部可删除 AI 数据，并匿名化必须保留的交易事实。' : '导出文件包含当前账号可导出的 AI 数据，完成后仅提供短期下载。' }}
      </p>
      <el-form label-position="top" @submit.prevent="submitJob">
        <el-form-item label="输入当前账号密码进行二次确认">
          <el-input v-model="confirmDialog.password" type="password" show-password autocomplete="current-password" @keyup.enter="submitJob" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmDialog.show = false">取消</el-button>
        <el-button :type="confirmDialog.type === 'DELETE' ? 'danger' : 'primary'" :loading="!!creatingType" @click="submitJob">
          {{ confirmDialog.type === 'DELETE' ? '确认申请删除' : '确认申请导出' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { DataAnalysis, Delete, Download, Refresh } from '@element-plus/icons-vue';
import { privacyApi, type PrivacyJob, type PrivacyJobStatus, type PrivacyJobType } from '@/api/modules';
import { clearIdempotencyKey, getOrCreateIdempotencyKey } from '@/utils/idempotency';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import {
  PRIVACY_JOB_REFRESH_MS,
  canDownloadPrivacyJob,
  canRetryPrivacyJob,
  isPrivacyJobActive,
  privacyJobStatusLabel,
  privacyJobTypeLabel,
  privacyStepLabel
} from '@/utils/privacyJobs';

const jobs = ref<PrivacyJob[]>([]);
const loading = ref(false);
const creatingType = ref<PrivacyJobType | ''>('');
const retryingId = ref('');
const downloadingId = ref('');
const confirmDialog = reactive<{ show: boolean; type: PrivacyJobType; password: string }>({ show: false, type: 'EXPORT', password: '' });
let refreshTimer: number | undefined;

const hasActiveJobs = computed(() => jobs.value.some(isPrivacyJobActive));
const typeLabel = privacyJobTypeLabel;
const statusLabel = privacyJobStatusLabel;
const stepLabel = privacyStepLabel;
const canRetry = canRetryPrivacyJob;
const canDownload = canDownloadPrivacyJob;
const statusTagType = (status: PrivacyJobStatus) => status === 'COMPLETED' ? 'success' : status === 'FAILED' ? 'danger' : status === 'RUNNING' ? 'warning' : 'info';
const formatTime = (value?: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';

const scheduleRefresh = () => {
  if (refreshTimer) window.clearTimeout(refreshTimer);
  refreshTimer = undefined;
  if (hasActiveJobs.value) refreshTimer = window.setTimeout(() => void loadJobs(false), PRIVACY_JOB_REFRESH_MS);
};

const loadJobs = async (showLoading = true) => {
  if (showLoading) loading.value = true;
  try { jobs.value = (await privacyApi.listJobs(50)) || []; } finally { loading.value = false; scheduleRefresh(); }
};

const openConfirmation = async (type: PrivacyJobType) => {
  if (type === 'DELETE') {
    const ok = await confirmAction('此操作会启动不可逆的 AI 数据删除任务。订单、支付等必须保留的数据将解除 AI 关联并匿名化。', {
      title: '删除范围确认',
      confirmButtonText: '继续二次确认',
      type: 'warning'
    });
    if (!ok) return;
  }
  confirmDialog.type = type;
  confirmDialog.password = '';
  confirmDialog.show = true;
};

const submitJob = async () => {
  const password = confirmDialog.password.trim();
  if (!password) return toast.warning('请输入当前账号密码');
  const type = confirmDialog.type;
  const scope = type === 'DELETE' ? 'privacy.delete' : 'privacy.export';
  const payload = { jobType: type, scope: 'AI_DOMAIN_V1' };
  const key = getOrCreateIdempotencyKey(scope, payload);
  creatingType.value = type;
  try {
    const job = type === 'DELETE'
      ? await privacyApi.createDeletion(password, key)
      : await privacyApi.createExport(password, key);
    clearIdempotencyKey(scope, payload);
    confirmDialog.show = false;
    confirmDialog.password = '';
    jobs.value = [job, ...jobs.value.filter((item) => item.jobId !== job.jobId)];
    toast.success(type === 'DELETE' ? 'AI 数据删除任务已提交' : 'AI 数据导出任务已提交');
    scheduleRefresh();
  } finally { creatingType.value = ''; }
};

const retry = async (job: PrivacyJob) => {
  retryingId.value = job.jobId;
  try {
    const updated = await privacyApi.retryJob(job.jobId);
    jobs.value = jobs.value.map((item) => item.jobId === updated.jobId ? updated : item);
    toast.success('任务已重新进入处理队列');
    scheduleRefresh();
  } finally { retryingId.value = ''; }
};

const download = async (job: PrivacyJob) => {
  downloadingId.value = job.jobId;
  try {
    const blob = await privacyApi.downloadExport(job.jobId);
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = `ai-data-export-${job.jobId}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  } finally { downloadingId.value = ''; }
};

onMounted(() => void loadJobs());
onUnmounted(() => { if (refreshTimer) window.clearTimeout(refreshTimer); });
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.privacy-page { display: flex; flex-direction: column; gap: 12px; padding-bottom: 24px; }
.scope-panel, .action-panel, .jobs-panel { border: 1px solid $color-border; border-radius: $radius-card; background: $color-card; }
.scope-panel { padding: 16px; }
.scope-panel > header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.scope-icon { display: grid; flex: 0 0 42px; height: 42px; place-items: center; border-radius: 8px; background: $color-primary-soft; color: $color-primary; font-size: 21px; }
h2, h3, p { margin: 0; }
h2 { color: $color-text-title; font-size: 16px; }
h3 { color: $color-text-title; font-size: 14px; }
header p, article p, .jobs-head p { margin-top: 4px; color: $color-text-muted; font-size: 12px; line-height: 1.55; }
.scope-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.scope-grid > div { display: flex; flex-direction: column; gap: 5px; padding: 11px; border: 1px solid $color-border-light; background: $color-bg-subtle; }
.scope-grid b { font-size: 13px; color: $color-text-title; }
.scope-grid span { color: $color-text-body; font-size: 12px; line-height: 1.6; }
.action-panel { overflow: hidden; }
.action-panel article { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 15px 16px; }
.action-panel article + article { border-top: 1px solid $color-border; }
.jobs-panel { padding: 16px; }
.jobs-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.job-list { display: flex; flex-direction: column; }
.job-row { display: flex; align-items: center; gap: 16px; padding: 14px 0; }
.job-row + .job-row { border-top: 1px solid $color-border-light; }
.job-main { flex: 1; min-width: 0; }
.job-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.job-id { display: block; margin: 5px 0 10px; overflow: hidden; color: $color-text-disabled; font-family: monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.job-step, .job-error, .job-expiry { margin-top: 6px; font-size: 12px; }
.job-step, .job-expiry { color: $color-text-muted; }
.job-error { color: $color-error; }
.job-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.confirm-copy { margin-bottom: 14px; color: $color-text-body; font-size: 13px; line-height: 1.65; }
@media (max-width: 700px) { .scope-grid { grid-template-columns: 1fr; } .action-panel article, .job-row { align-items: flex-start; flex-direction: column; } .action-panel .el-button, .job-actions { width: 100%; } .job-actions .el-button { flex: 1; } }
</style>
