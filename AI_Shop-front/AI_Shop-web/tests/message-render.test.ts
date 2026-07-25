import { describe, expect, it } from 'vitest';
import { renderAgentMessageHtml, sanitizeLogisticsTableHtml } from '@/utils/agentMessageRender';

describe('agent message rendering', () => {
  it('does not execute arbitrary html or script content', () => {
    const html = renderAgentMessageHtml('<script>alert(1)</script><img src=x onerror=alert(2)>');
    expect(html).not.toContain('<script');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
  });

  it('keeps only text from logistics table cells', () => {
    const html = sanitizeLogisticsTableHtml(
      '<table><tr><td><b>已发货</b><img src=x onerror=alert(1)></td></tr></table>'
    );
    expect(html).toContain('已发货');
    expect(html).not.toContain('<b>');
    expect(html).not.toContain('onerror');
  });
});
