export type SortMode = '' | 'price-asc' | 'price-desc' | 'sale';
export type ProductSortKey = '' | 'PRICE' | 'SALE';
export type SortDirection = '' | 'ASC' | 'DESC';

export const sortModeToQuery = (mode: SortMode): {
  sortKey: ProductSortKey;
  sortDirection: SortDirection;
} => {
  switch (mode) {
    case 'price-asc':
      return { sortKey: 'PRICE', sortDirection: 'ASC' };
    case 'price-desc':
      return { sortKey: 'PRICE', sortDirection: 'DESC' };
    case 'sale':
      return { sortKey: 'SALE', sortDirection: '' };
    default:
      return { sortKey: '', sortDirection: '' };
  }
};

export const sortQueryToMode = (
  sortKey: ProductSortKey | string | undefined,
  sortDirection: SortDirection | string | undefined
): SortMode => {
  if (sortKey === 'PRICE') {
    return sortDirection === 'ASC' ? 'price-asc' : 'price-desc';
  }
  if (sortKey === 'SALE') return 'sale';
  return '';
};
