import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/vue';
import BrandMark from '@/components/common/BrandMark.vue';

describe('SmartSelect brand marker', () => {
  afterEach(() => cleanup());

  it('exposes the new brand name and selection mark', () => {
    const { getByRole } = render(BrandMark);
    const mark = getByRole('img', { name: '智选 SmartSelect' });

    expect(mark.querySelector('rect')).toHaveAttribute('fill', '#0f766e');
    expect(mark.querySelector('path')).toHaveAttribute('stroke', '#ffffff');
  });

  it('keeps the light variant legible on the admin/auth surfaces', () => {
    const { getByRole } = render(BrandMark, { props: { variant: 'light' } });
    const mark = getByRole('img', { name: '智选 SmartSelect' });

    expect(mark.querySelector('rect')).toHaveAttribute('fill', '#ffffff');
    expect(mark.querySelector('path')).toHaveAttribute('stroke', '#0f766e');
  });
});
