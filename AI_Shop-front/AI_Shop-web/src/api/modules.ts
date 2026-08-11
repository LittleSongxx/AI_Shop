import request from './http';
import { withCache } from '@/utils/apiCache';
import type { RecommendationAttribution } from '@/utils/recommendationAttribution';

export { locationApi } from './location';
export type { LocationPayload, LocationWeatherPayload } from './location';

const CACHE_KEYS = {
  CATEGORY: 'product:category:v2',
  COMMEND: 'product:commend'
} as const;

const productDetailKey = (id: string) => `product:detail:${id}`;

export const accountApi = {
  checkCode: () => request.get('/account/checkCode'),
  login: (params: Record<string, unknown>) => request.postForm('/account/login', params),
  register: (params: Record<string, unknown>) => request.postForm('/account/register', params),
  autoLogin: () => request.get('/account/autoLogin'),
  logout: () => request.postForm('/account/logout'),
  getUserInfo: () => request.get('/account/getUserInfo'),
  updateUserInfo: (params: Record<string, unknown>) => request.postForm('/account/updateUserInfo', params),
  updatePassword: (params: Record<string, unknown>) => request.postForm('/account/updatePassword', params),
  getEmailCode: (params: Record<string, unknown>) => request.postForm('/account/getEmailCode', params),
  forgetPassword: (params: Record<string, unknown>) => request.postForm('/account/forgetPassword', params)
};

export const searchApi = {
  loadHotKeywords: () => request.get('/search/loadHotKeywords'),
  loadRecentKeywords: () => request.get('/search/loadRecentKeywords'),
  saveKeyword: (keyword: string) => request.postForm('/search/saveKeyword', { keyword }),
  clearRecentKeywords: () => request.postForm('/search/clearRecentKeywords'),
  removeRecentKeyword: (keyword: string) => request.postForm('/search/removeRecentKeyword', { keyword }),
  loadGuessKeywords: () => request.get('/search/loadGuessKeywords'),
  loadRecommendProducts: (limit = 8) => request.get('/search/loadRecommendProducts', { params: { limit } })
};

export const productApi = {

  loadCategory: () =>
    withCache(() => request.get('/product/loadCategory'), {
      key: CACHE_KEYS.CATEGORY,
      ttl: 30 * 60 * 1000
    }),

  loadCommendProduct: () =>
    withCache(() => request.get('/product/loadCommendProduct'), {
      key: CACHE_KEYS.COMMEND,
      ttl: 5 * 60 * 1000
    }),
  loadProduct: (params: Record<string, unknown>) => request.postForm('/product/loadProduct', params),

  getProduct: (productId: string) =>
    withCache(() => request.postForm('/product/getProduct', { productId }), {
      key: productDetailKey(productId),
      ttl: 2 * 60 * 1000
    }),
  searchProducts: (params: Record<string, unknown>) => request.postForm('/search/products', params)
};

export const cartApi = {
  add2Cart: (params: Record<string, unknown>) => request.postForm('/productCart/add2Cart', params),
  loadProductCart: (params: Record<string, unknown>) => request.postForm('/productCart/loadProductCart', params),
  deleteCart: (cartId: string) => request.postForm('/productCart/deleteCart', { cartId })
};

export const orderApi = {
  postOrder: (payload: Record<string, unknown>, idempotencyKey: string) =>
    request.post('/order/postOrder', payload, {
      headers: { 'Idempotency-Key': idempotencyKey }
    }),
  getPayInfo: (orderId: string) => request.postForm('/order/getPayInfo', { orderId }),
  getOrderInfo: (payOrderId: string) => request.postForm('/order/getOrderInfo', { payOrderId }),
  loadMyOrder: (params: Record<string, unknown>) => request.postForm('/order/loadMyOrder', params),
  cancelOrder: (orderId: string) => request.postForm('/order/cancelOrder', { orderId }),
  deleteOrder: (orderId: string) => request.postForm('/order/deleteOrder', { orderId }),
  confirmOrder: (orderId: string) => request.postForm('/order/confirmOrder', { orderId }),
  getMyOrderDetail: (orderId: string) => request.postForm('/order/getMyOrderDetail', { orderId }),
  getLogistics: (orderId: string) => request.postForm('/order/getLogistics', { orderId }),
  getOrderCountInfo: () => request.get('/order/getOrderCountInfo'),
  refundOrder: (orderItemId: string) => request.postForm('/order/refundOrder', { orderItemId })
};

export const addressApi = {
  loadDataList: () => request.get('/userAddress/loadDataList'),
  addAddress: (params: Record<string, unknown>) => request.postForm('/userAddress/addAddress', params),
  updateAddress: (params: Record<string, unknown>) => request.postForm('/userAddress/updateAddress', params),
  delAddress: (addressId: string) => request.postForm('/userAddress/delAddress', { addressId }),
  updateDefault: (addressId: string) => request.postForm('/userAddress/updateDefault', { addressId })
};

export const commentApi = {
  loadComment: (params: Record<string, unknown>) => request.postForm('/order/comment/loadComment', params),
  getProductCommentStats: (productId: string) =>
    request.postForm('/order/comment/getProductCommentStats', { productId }),
  getComment: (orderId: string) => request.postForm('/order/comment/getComment', { orderId }),
  postComment: (params: Record<string, unknown>) => request.postForm('/order/comment/postComment', params),
  postReComment: (params: Record<string, unknown>) => request.postForm('/order/comment/postReComment', params),
  loadMyComment: (params: Record<string, unknown>) => request.postForm('/order/comment/loadMyComment', params),
  delMyComment: (orderId: string) => request.postForm('/order/comment/delMyComment', { orderId })
};

export const commentReportApi = {
  submitReport: (params: Record<string, unknown>) => request.postForm('/commentReport/submitReport', params)
};

export const couponApi = {
  loadDiscountCoupon: (params: Record<string, unknown>) =>
    request.postForm('/discountCoupon/loadDiscountCoupon', params),
  rushCoupon: (couponId: string, idempotencyKey: string) =>
    request.postForm(
      '/discountCoupon/rushCoupon',
      { couponId },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),
  buyDiscountCoupon: (couponId: string, payMethod: string, idempotencyKey: string) =>
    request.postForm(
      '/discountCoupon/buyDiscountCoupon',
      { couponId, payMethod },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),
  loadUserCoupon: (params: Record<string, unknown>) =>
    request.postForm('/discountCoupon/loadUserCoupon', params),
  getDiscountCouponDetail: (couponId: string) =>
    request.postForm('/discountCoupon/getDiscountCouponDetail', { couponId })
};

const reportAgentProductClick = async (
  productId: string,
  requestId: string,
  position: number
): Promise<RecommendationAttribution> => {
  const payload = new FormData();
  payload.append('productId', productId);
  payload.append('requestId', requestId);
  payload.append('position', String(position));

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 800);
  try {
    const response = await fetch('/api/agent/reportClick', {
      method: 'POST',
      body: payload,
      credentials: 'include',
      signal: controller.signal
    });
    if (!response.ok) {
      throw new Error(`Click attribution failed with HTTP ${response.status}`);
    }
    const result = await response.json();
    if (result?.code !== 200 || !result?.data) {
      throw new Error(result?.info || 'Click attribution was rejected');
    }
    const attribution = result.data as RecommendationAttribution;
    if (
      attribution.requestId !== requestId ||
      attribution.productId !== productId ||
      Number(attribution.position) !== position
    ) {
      throw new Error('Click attribution response mismatch');
    }
    return attribution;
  } finally {
    window.clearTimeout(timeout);
  }
};

export const agentApi = {
  loadHistoryMessage: (params: Record<string, unknown>) => request.postForm('/agent/loadHistoryMessage', params),
  clearHistoryMessage: () =>
    request.postForm<{ clearedThroughMessageId: number; memoryPreserved: boolean }>(
      '/agent/clearHistoryMessage',
      {}
    ),
  sendMessage: (
    message: string,
    fromProduct?: boolean,
    consultProductId?: string,
    options?: {
      imageAssetId?: string;
      comparisonProductIds?: string[];
    }
  ) =>
    request.postForm('/agent/sendMessage', {
      message,
      fromProduct,
      consultProductId,
      imageAssetId: options?.imageAssetId,
      comparisonProductIds: options?.comparisonProductIds?.length
        ? JSON.stringify(options.comparisonProductIds)
        : undefined
    }),
  getShoppingProfile: () => request.get<ShoppingProfile>('/agent/shoppingProfile'),
  updateShoppingProfile: (expectedRevision: number, profile: Partial<ShoppingProfile>) =>
    request.post<ShoppingProfile>('/agent/shoppingProfile/update', { expectedRevision, profile }),
  clearShoppingProfile: (expectedRevision: number) =>
    request.post<ShoppingProfile>('/agent/shoppingProfile/clear', { expectedRevision }),
  setShoppingPersonalization: (expectedRevision: number, enabled: boolean) =>
    request.post<ShoppingProfile>('/agent/shoppingProfile/personalization', { expectedRevision, enabled }),
  deleteShoppingSignal: (expectedRevision: number, signalId: string) =>
    request.post<ShoppingProfile>('/agent/shoppingProfile/signals/delete', { expectedRevision, signalId }),
  clearShoppingSignals: (expectedRevision: number) =>
    request.post<ShoppingProfile>('/agent/shoppingProfile/signals/clear', { expectedRevision }),
  listSupportCases: (limit = 20) => request.get<SupportCase[]>('/agent/supportCases', { params: { limit } }),
  getSupportCase: (caseId: string) =>
    request.get<SupportCase>('/agent/supportCaseDetail', { params: { caseId } }),
  selectOrderCandidate: (selectionId: string, targetType: 'ORDER' | 'ORDER_ITEM', targetId: string) =>
    request.postForm('/agent/selectOrderCandidate', { selectionId, targetType, targetId }),
  selectVisualSubject: (selectionId: string, subjectId: string) =>
    request.postForm('/agent/selectVisualSubject', { selectionId, subjectId }),
  cancelMessage: (messageId: number, assistantMessage?: string) =>
    request.postForm('/agent/cancelMessage', { messageId, assistantMessage }),
  reportClick: reportAgentProductClick,
  clearProductConsult: () => request.postForm('/agent/clearProductConsult', {}),
  pauseProductConsult: () => request.postForm('/agent/pauseProductConsult', {}),
  getProductConsultContext: () =>
    request.postForm<{ productId?: string; productName?: string; active?: boolean } | null>(
      '/agent/getProductConsultContext',
      {}
    ),
  requestHuman: (reason?: string, sourceMessageId?: number) =>
    request.postForm('/agent/requestHuman', { reason, sourceMessageId }),
  cancelHuman: () => request.postForm('/agent/cancelHuman', {}),
  humanStatus: () => request.postForm('/agent/humanStatus', {}),
  feedback: (messageId: number, rating: 1 | -1, reason?: string, detail?: string) =>
    request.postForm('/agent/feedback', { messageId, rating, reason, detail }),
  confirmAction: (actionToken: string) =>
    request.postForm<{ actionType?: string; success?: boolean; resultMessage?: string }>(
      '/agent/confirmAction',
      { actionToken }
    ),
  cancelAction: (actionToken: string) => request.postForm('/agent/cancelAction', { actionToken })
};

export const signApi = {
  getSignCalendar: (yearMonth: string) => request.postForm('/sign/getSignCalendar', { yearMonth }),
  sign: () => request.postForm('/sign/sign'),
  msign: (date: string) => request.postForm('/sign/msign', { date })
};

export const favoriteApi = {
  loadFavorite: (params: Record<string, unknown>) => request.postForm('/userFavorite/loadFavorite', params),
  toggleFavorite: (productId: string) => request.postForm('/userFavorite/toggleFavorite', { productId }),
  isFavorite: (productId: string) => request.postForm('/userFavorite/isFavorite', { productId }),
  removeFavorite: (favoriteId: string) => request.postForm('/userFavorite/removeFavorite', { favoriteId })
};

export const browseApi = {
  loadBrowse: (params: Record<string, unknown>) => request.postForm('/browseHistory/loadBrowse', params),
  clearBrowse: () => request.postForm('/browseHistory/clearBrowse'),
  removeBrowse: (historyId: number) => request.postForm('/browseHistory/removeBrowse', { historyId })
};

export const userMemberApi = {
  getProfile: () => request.get('/userMember/getProfile'),
  getProfileWithCenter: () => request.get('/userMember/getProfile', { params: { center: true } }),
  getMemberCenter: () => request.get('/userMember/getMemberCenter'),
  loadMemberCenter: () => request.get('/userMember/loadMemberCenter'),
  claimLevelReward: (levelCode: number) =>
    request.postForm('/userMember/claimLevelReward', { levelCode }),
  getLevelBadge: (userId: string) => request.get('/userMember/getLevelBadge', { params: { userId } })
};

export const notificationApi = {
  loadNotification: (params: Record<string, unknown>) =>
    request.postForm('/userNotification/loadNotification', params),
  countUnread: () => request.get('/userNotification/countUnread'),
  markRead: (notificationId: string) => request.postForm('/userNotification/markRead', { notificationId }),
  markAllRead: () => request.postForm('/userNotification/markAllRead'),
  deleteNotification: (notificationId: string) =>
    request.postForm('/userNotification/deleteNotification', { notificationId }),
  clearAll: () => request.postForm('/userNotification/clearAll'),
  getPopupNotification: () => request.get('/userNotification/getPopupNotification'),
  clearPopupNotification: (notificationId: string) =>
    request.postForm('/userNotification/clearPopupNotification', { notificationId })
};

export const payTradeApi = {
  loadMyTrades: (pageNo: number) => request.postForm('/payTrade/loadMyTrades', { pageNo })
};

export interface ImageUploadResult {
  path: string;
  assetId?: string;
  contentSha256?: string;
  mimeType?: string;
  width?: number;
  height?: number;
  expiresAt?: string;
  pendingReview?: boolean;
  moderationId?: number;
  moderationStatus?: string;
  scene?: string;
}

export interface ImageUploadOptions {

  skipPrepare?: boolean;
}

export const fileApi = {
  uploadImage: async (
    file: Blob,
    createThumbnail = true,
    scene?: 'avatar' | 'comment' | 'agent',
    orderId?: string,
    options?: ImageUploadOptions
  ): Promise<ImageUploadResult> => {
    const { prepareForUpload } = await import('@/utils/imageUpload');
    const prepared = options?.skipPrepare ? file : await prepareForUpload(file);
    if (!prepared || prepared.size < 1024) {
      throw new Error('图片导出失败，请重试');
    }
    const ext = prepared.type === 'image/png' ? 'png' : 'jpg';
    const formData = new FormData();
    formData.append('file', prepared, file instanceof File ? file.name.replace(/\.\w+$/, `.${ext}`) : `image.${ext}`);
    formData.append('createThumbnail', String(createThumbnail));
    if (scene) formData.append('scene', scene);
    if (orderId) formData.append('orderId', orderId);
    const response = await fetch('/api/file/uploadImage', {
      method: 'POST',
      body: formData,
      credentials: 'include'
    });
    const result = await response.json();
    if (result.code !== 200) {
      const { ApiBusinessError } = await import('@/utils/apiError');
      const payload = result.data || {};
      throw new ApiBusinessError(result.info || '上传失败', {
        code: result.code,
        errorType: payload.errorType,
        unbanAt: payload.unbanAt
      });
    }
    const data = result.data;
    const { normalizeCommentImagePath } = await import('@/utils/commentImagePaths');
    const path = normalizeCommentImagePath(data) || '';
    const assetId =
      typeof data === 'object' && data !== null && (data as { assetId?: unknown }).assetId
        ? String((data as { assetId?: unknown }).assetId)
        : undefined;
    if (scene === 'agent' && !assetId) {
      throw new Error('上传响应缺少图片资产ID');
    }
    if (scene !== 'agent' && !path) {
      throw new Error('上传响应缺少图片路径');
    }
    const pendingReview =
      typeof data === 'object' && data !== null && 'pendingReview' in data
        ? !!(data as { pendingReview?: boolean }).pendingReview
        : false;
    const moderationId =
      typeof data === 'object' && data !== null && (data as { moderationId?: unknown }).moderationId != null
        ? Number((data as { moderationId?: unknown }).moderationId)
        : undefined;
    const moderationStatus =
      typeof data === 'object' && data !== null && (data as { moderationStatus?: unknown }).moderationStatus
        ? String((data as { moderationStatus?: unknown }).moderationStatus)
        : undefined;
    const sceneValue =
      typeof data === 'object' && data !== null && (data as { scene?: unknown }).scene
        ? String((data as { scene?: unknown }).scene)
        : undefined;
    const contentSha256 =
      typeof data === 'object' && data !== null && (data as { contentSha256?: unknown }).contentSha256
        ? String((data as { contentSha256?: unknown }).contentSha256)
        : undefined;
    const mimeType =
      typeof data === 'object' && data !== null && (data as { mimeType?: unknown }).mimeType
        ? String((data as { mimeType?: unknown }).mimeType)
        : undefined;
    const width =
      typeof data === 'object' && data !== null && Number.isFinite(Number((data as { width?: unknown }).width))
        ? Number((data as { width?: unknown }).width)
        : undefined;
    const height =
      typeof data === 'object' && data !== null && Number.isFinite(Number((data as { height?: unknown }).height))
        ? Number((data as { height?: unknown }).height)
        : undefined;
    const expiresAt =
      typeof data === 'object' && data !== null && (data as { expiresAt?: unknown }).expiresAt
        ? String((data as { expiresAt?: unknown }).expiresAt)
        : undefined;
    return {
      path,
      assetId,
      contentSha256,
      mimeType,
      width,
      height,
      expiresAt,
      pendingReview,
      moderationId: Number.isFinite(moderationId) ? moderationId : undefined,
      moderationStatus,
      scene: sceneValue
    };
  }
};

export interface ShoppingProfile {
  version?: number;
  revision: number;
  category?: string | null;
  budgetMin?: number | null;
  budgetMax?: number | null;
  brands: string[];
  excludedBrands: string[];
  scenarios: string[];
  features: string[];
  acceptSubstitute?: boolean | null;
  personalizationEnabled?: boolean;
  implicitSignals?: Array<{
    signalId: string;
    kind: string;
    value: string;
    effectiveWeight?: number;
    count?: number;
    source?: string;
    observedAt?: string;
  }>;
  fieldMeta?: Record<string, unknown>;
}

export interface SupportCase {
  caseId: string | number;
  caseNo?: string;
  userId?: string;
  orderId?: string;
  orderItemId?: string;
  category?: string;
  categoryLabel?: string;
  status?: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CANCELLED' | string;
  description?: string;
  evidence?: {
    path?: string;
    moderationId?: number;
    moderationStatus?: string;
    scene?: string;
    vlmStatus?: string;
    vlmDescription?: string;
  };
  supportSessionId?: string;
  assignedAdmin?: string;
  resolutionCode?: string;
  rootCause?: string;
  resolutionSummary?: string;
  createdAt?: string;
  updatedAt?: string;
  resolvedAt?: string;
}
