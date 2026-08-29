import { describe, expect, it } from 'vitest';

import {
  AGENT_ACTION_STATUS,
  agentActionStatusClass,
  agentActionStatusLabel,
  normalizeAgentActionStatus
} from '../src/utils/agentActionStatus';

describe('agent action uncertain states', () => {
  it('keeps inconclusive and manual review out of the pending state', () => {
    expect(normalizeAgentActionStatus(6)).toBe(AGENT_ACTION_STATUS.INCONCLUSIVE);
    expect(normalizeAgentActionStatus(7)).toBe(AGENT_ACTION_STATUS.MANUAL_REVIEW);
    expect(agentActionStatusClass(6)).toBe('is-executing');
    expect(agentActionStatusClass(7)).toBe('is-manual-review');
  });

  it('shows non-retryable status labels for both uncertain states', () => {
    expect(agentActionStatusLabel(6)).toContain('核对中');
    expect(agentActionStatusLabel(7)).toContain('人工复核');
  });

  it('accepts server status names used by reconciliation responses', () => {
    expect(normalizeAgentActionStatus('INCONCLUSIVE')).toBe(AGENT_ACTION_STATUS.INCONCLUSIVE);
    expect(normalizeAgentActionStatus('MANUAL_REVIEW')).toBe(AGENT_ACTION_STATUS.MANUAL_REVIEW);
  });
});
