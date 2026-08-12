import type { PrivacyJob, PrivacyJobStatus, PrivacyJobType } from '@/api/modules';

export const PRIVACY_JOB_REFRESH_MS = 5000;

export function privacyJobTypeLabel(value: PrivacyJobType): string {
  return value === 'DELETE' ? '删除 AI 数据' : '导出 AI 数据';
}

export function privacyJobStatusLabel(value: PrivacyJobStatus): string {
  return ({
    PENDING: '等待处理',
    RUNNING: '处理中',
    COMPLETED: '已完成',
    FAILED: '处理失败'
  } as Record<PrivacyJobStatus, string>)[value] || value;
}

export function privacyStepLabel(value?: string | null): string {
  return ({
    PREPARE_EXPORT: '整理数据范围',
    WRITE_EXPORT: '生成导出文件',
    FINALIZE_EXPORT: '设置短期下载',
    REVOKE_EXPORTS: '撤销历史导出',
    DETACH_RETAINED_FACTS: '匿名化法定保留数据',
    DELETE_SUPPORT_DATA: '删除客服与售后 AI 数据',
    DELETE_PERSONALIZATION: '删除画像与长期记忆',
    DELETE_RUNTIME_DATA: '删除运行时与归因数据',
    DELETE_TRACES_MESSAGES: '删除对话、摘要与 Trace',
    CLEAR_CACHES: '清理缓存'
  } as Record<string, string>)[String(value || '')] || '等待处理';
}

export function isPrivacyJobActive(job: PrivacyJob): boolean {
  return job.status === 'PENDING' || job.status === 'RUNNING';
}

export function canRetryPrivacyJob(job: PrivacyJob): boolean {
  return job.status === 'FAILED';
}

export function canDownloadPrivacyJob(job: PrivacyJob): boolean {
  return job.jobType === 'EXPORT' && job.status === 'COMPLETED' && job.downloadable;
}
