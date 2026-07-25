import { describe, expect, it } from 'vitest';

import { sortModeToQuery, sortQueryToMode } from '@/utils/productSort';

describe('product sort contract', () => {
  it('maps UI modes only to the backend enum contract', () => {
    expect(sortModeToQuery('price-asc')).toEqual({
      sortKey: 'PRICE',
      sortDirection: 'ASC'
    });
    expect(sortModeToQuery('price-desc')).toEqual({
      sortKey: 'PRICE',
      sortDirection: 'DESC'
    });
    expect(sortModeToQuery('sale')).toEqual({
      sortKey: 'SALE',
      sortDirection: ''
    });
  });

  it('does not preserve unknown fields or directions', () => {
    expect(sortQueryToMode('PRICE', 'ASC')).toBe('price-asc');
    expect(sortQueryToMode('PRICE', 'DROP TABLE')).toBe('price-desc');
    expect(sortQueryToMode('random', 'ASC')).toBe('');
  });
});
