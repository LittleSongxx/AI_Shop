<template>
  <div class="notification-page">
    <div class="page-toolbar">
      <h2 class="page-title">消息中心</h2>
      <div v-if="list.length" class="toolbar-actions">
        <button type="button" class="link-btn" @click="markAll">全部已读</button>
        <button type="button" class="link-btn danger" @click="clearAll">清空</button>
      </div>
    </div>
    <div v-if="loading && !list.length" class="loading-tip">加载中…</div>
    <div v-else-if="loadError && !list.length" class="load-error">
      <p>{{ loadError }}</p>
      <el-button type="primary" size="small" @click="retryLoad">重试</el-button>
    </div>
    <ul v-else-if="list.length" class="notify-list">
      <li v-for="item in list" :key="item.notificationId" class="notify-row">
        <SwipeDeleteRow
          v-if="useSwipeDelete"
          :open="openSwipeId === item.notificationId"
          @open="openSwipeId = item.notificationId"
          @close="onSwipeClose(item.notificationId)"
          @delete="remove(item.notificationId)"
        >
          <article
            class="notify-item"
            :class="{ unread: item.readStatus === 0 }"
            @click="openItem(item)"
          >
            <div class="notify-head">
              <span class="notify-title">{{ item.title }}</span>
              <span class="notify-time">{{ formatTime(item.createTime) }}</span>
            </div>
            <p class="notify-content">{{ item.content }}</p>
          </article>
        </SwipeDeleteRow>

        <article
          v-else
          class="notify-item notify-item--desktop"
          :class="{ unread: item.readStatus === 0 }"
        >
          <div class="notify-main" @click="openItem(item)">
            <div class="notify-head">
              <span class="notify-title">{{ item.title }}</span>
              <span class="notify-time">{{ formatTime(item.createTime) }}</span>
            </div>
            <p class="notify-content">{{ item.content }}</p>
          </div>
          <el-button
            link
            type="danger"
            class="btn-delete"
            @click.stop="remove(item.notificationId)"
          >
            删除
          </el-button>
        </article>
      </li>
    </ul>
    <p v-else class="empty-tip">暂无消息</p>
    <div v-if="hasMore" class="load-more">
      <el-button text @click="loadMore">加载更多</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import SwipeDeleteRow from '@/components/business/SwipeDeleteRow.vue';
import { notificationApi } from '@/api/modules';
import { useDevice } from '@/composables/useDevice';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { navigateNotification, type NotificationData } from '@/utils/notification';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import { formatDisplayDateTime } from '@/utils/formatDateTime';

const router = useRouter();
const { isDesktop } = useDevice();
const useSwipeDelete = computed(() => !isDesktop.value);
const { refreshUnreadCount } = useUnreadCount();
const list = ref<any[]>([]);
const pageNo = ref(1);
const total = ref(0);
const loading = ref(false);
const loadError = ref('');
const openSwipeId = ref<string | null>(null);

const hasMore = computed(() => list.value.length < total.value);

const load = async (append = false) => {
  if (!append) loadError.value = '';
  loading.value = true;
  try {
    const res: any = await notificationApi.loadNotification({ pageNo: pageNo.value });
    const rows = res?.list || [];
    total.value = res?.totalCount ?? rows.length;
    list.value = append ? [...list.value, ...rows] : rows;
  } catch (e: any) {
    if (!append) {
      loadError.value = e?.info || e?.message || '消息加载失败，请稍后重试';
      list.value = [];
    }
  } finally {
    loading.value = false;
  }
};

const retryLoad = () => {
  pageNo.value = 1;
  void load();
};

const loadMore = async () => {
  pageNo.value += 1;
  await load(true);
};

const markAll = async () => {
  await notificationApi.markAllRead();
  list.value = list.value.map((n) => ({ ...n, readStatus: 1 }));
  await refreshUnreadCount();
  toast.success('已全部标为已读');
};

const onSwipeClose = (id: string) => {
  if (openSwipeId.value === id) openSwipeId.value = null;
};

const remove = async (notificationId: string) => {
  const item = list.value.find((n) => n.notificationId === notificationId);
  await notificationApi.deleteNotification(notificationId);
  list.value = list.value.filter((n) => n.notificationId !== notificationId);
  total.value = Math.max(0, total.value - 1);
  if (openSwipeId.value === notificationId) openSwipeId.value = null;
  if (item?.readStatus === 0) await refreshUnreadCount();
  toast.success('已删除');
};

const clearAll = async () => {
  const ok = await confirmAction('确定清空全部消息吗？', {
    title: '清空消息',
    confirmButtonText: '清空'
  });
  if (!ok) return;
  await notificationApi.clearAll();
  list.value = [];
  total.value = 0;
  openSwipeId.value = null;
  await refreshUnreadCount();
  toast.success('已清空');
};

const openItem = async (item: NotificationData) => {
  await navigateNotification(router, item, { refreshUnread: refreshUnreadCount });
};

const formatTime = (t: string | number) => formatDisplayDateTime(t);

onMounted(() => load());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.notification-page {
  padding: 12px 16px 24px;
  max-width: 720px;
  margin: 0 auto;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;
}

.page-title {
  margin: 0;
  font-size: 18px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.link-btn {
  border: none;
  background: none;
  color: $color-primary;
  cursor: pointer;
  font-size: 14px;
  padding: 0;

  &.danger {
    color: $color-error;
  }
}

.notify-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.notify-row {
  border-bottom: 1px solid $color-border;
}

.notify-item {
  padding: 14px 16px;
  cursor: pointer;
  background: $color-bg-subtle;

  &.unread {
    background: #ffffff;
  }

  &--desktop {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    cursor: default;

    .notify-main {
      flex: 1;
      min-width: 0;
      cursor: pointer;
    }

    .btn-delete {
      flex-shrink: 0;
      margin-top: 2px;
      padding: 0 4px;
    }
  }
}

.notify-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.notify-title {
  font-weight: 600;
  font-size: 15px;
}

.notify-time {
  font-size: 12px;
  color: $color-text-secondary;
  flex-shrink: 0;
}

.notify-content {
  margin: 6px 0 0;
  font-size: 14px;
  color: $color-text-secondary;
}

.empty-tip,
.loading-tip,
.load-error {
  text-align: center;
  color: $color-text-secondary;
  padding: 40px 0;
}

.load-error p {
  margin: 0 0 12px;
}

.load-more {
  text-align: center;
  margin-top: 12px;
}
</style>
