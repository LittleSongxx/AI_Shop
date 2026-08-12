import { describe, expect, it } from 'vitest';
import type { PrivacyJob } from '@/api/modules';
import {
  canDownloadPrivacyJob,
  canRetryPrivacyJob,
  isPrivacyJobActive,
  privacyJobStatusLabel,
  privacyStepLabel
} from '@/utils/privacyJobs';

const job = (values: Partial<PrivacyJob>): PrivacyJob => ({
  jobId: 'privacy-1',
  jobType: 'EXPORT',
  status: 'PENDING',
  steps: [],
  progress: { completed: 0, total: 3, percent: 0 },
  retryCount: 0,
  downloadable: false,
  ...values
});

describe('privacy job presentation rules', () => {
  it('only refreshes pending or running jobs', () => {
    expect(isPrivacyJobActive(job({ status: 'PENDING' }))).toBe(true);
    expect(isPrivacyJobActive(job({ status: 'RUNNING' }))).toBe(true);
    expect(isPrivacyJobActive(job({ status: 'COMPLETED' }))).toBe(false);
  });

  it('only enables actions in server-approved terminal states', () => {
    expect(canRetryPrivacyJob(job({ status: 'FAILED' }))).toBe(true);
    expect(canRetryPrivacyJob(job({ status: 'RUNNING' }))).toBe(false);
    expect(canDownloadPrivacyJob(job({ status: 'COMPLETED', downloadable: true }))).toBe(true);
    expect(canDownloadPrivacyJob(job({ jobType: 'DELETE', status: 'COMPLETED', downloadable: true }))).toBe(false);
  });

  it('renders lifecycle steps and failures without calling deletion a chat clear', () => {
    expect(privacyStepLabel('DELETE_TRACES_MESSAGES')).toContain('Trace');
    expect(privacyStepLabel('DETACH_RETAINED_FACTS')).toContain('匿名化');
    expect(privacyJobStatusLabel('FAILED')).toBe('处理失败');
  });
});
