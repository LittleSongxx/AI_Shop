<template>
  <PcAuthShell v-if="pcLayout === 'auth'" class="pc-sub-layout ignore pc-surface">
    <RouterView v-slot="{ Component }">
      <Transition name="page-fade" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
  </PcAuthShell>

  <div v-else class="pc-sub-layout ignore pc-surface">
    <SiteHeader />
    <main class="pc-sub-main">
      <div
        class="pc-sub-container"
        :class="{
          'is-user-center': pcLayout === 'user',
          'is-agent-pc': route.path === '/ai-assistant'
        }"
      >
        <div v-if="pcLayout === 'user'" class="user-center-layout">
          <PcUserSidebar />
          <div class="user-center-content">
            <header v-if="pageTitle" class="user-page-head">
              <h1>{{ pageTitle }}</h1>
            </header>
            <RouterView v-slot="{ Component }">
              <Transition name="page-fade" mode="out-in">
                <component :is="Component" />
              </Transition>
            </RouterView>
          </div>
        </div>

        <template v-else>
          <header v-if="showPlainPageHead" class="user-page-head plain-page-head">
            <h1>{{ pageTitle }}</h1>
          </header>
          <RouterView v-slot="{ Component }">
            <Transition name="page-fade" mode="out-in">
              <div
                class="pc-plain-body"
                :class="{
                  'is-agent-pc': route.path === '/ai-assistant',
                  'is-product-detail': isProductDetailPage
                }"
              >
                <component :is="Component" />
              </div>
            </Transition>
          </RouterView>
        </template>
      </div>
    </main>
    <AppFooter />
    <PcFloatToolbar />
    <NotificationPopup @click="handleNotificationClick" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';
import AppFooter from '@/components/layout/AppFooter.vue';
import NotificationPopup from '@/components/business/NotificationPopup.vue';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { navigateNotification, type NotificationData } from '@/utils/notification';
import PcAuthShell from '@/components/layout/PcAuthShell.vue';
import PcFloatToolbar from '@/components/layout/PcFloatToolbar.vue';
import PcUserSidebar from '@/components/layout/PcUserSidebar.vue';
import SiteHeader from '@/components/layout/SiteHeader.vue';
import { resolvePcLayoutMode } from '@/constants/pcUserNav';
import { useAuthStore } from '@/stores/auth';
import { useCartStore } from '@/stores/cart';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const cartStore = useCartStore();
const { refreshUnreadCount } = useUnreadCount();

const pcLayout = computed(() => resolvePcLayoutMode(route.path));
const pageTitle = computed(() => String(route.meta.title || ''));
const isProductDetailPage = computed(
  () => /^\/product\/[^/]+$/.test(route.path) && !route.path.endsWith('/comments')
);
const showPlainPageHead = computed(
  () => !!pageTitle.value && !route.meta.hidePcPageHead && !isProductDetailPage.value
);

onMounted(() => {
  if (authStore.isLoggedIn) cartStore.fetchCartCount();
});

const handleNotificationClick = (notification: NotificationData) => {
  void navigateNotification(router, notification, { refreshUnread: refreshUnreadCount });
};
</script>

<style scoped lang="scss">

@use '@/styles/variables' as *;

.pc-sub-layout.ignore {

  min-height: 100vh;

  min-height: var(--app-vh, 100dvh);

  display: flex;

  flex-direction: column;

  background: $color-bg;

  .pc-sub-main {

    flex: 1;

    padding: 16px 0 32px;

  }

  .pc-sub-container {

    width: 100%;

    max-width: $content-max-width;

    margin: 0 auto;

    padding: 0 16px;

    box-sizing: border-box;

    &.is-user-center {

      max-width: $content-max-width;

    }

    :deep(.page-nav-bar) {

      margin-bottom: 16px;

    }

  }

  .user-center-layout {

    display: flex;

    align-items: flex-start;

    gap: 16px;

  }

  .user-center-content {

    flex: 1;

    min-width: 0;

    background: $color-card;

    border-radius: $radius-card;

    box-shadow: $shadow-card;

    padding: 16px 20px 20px;

  }

  .user-page-head {

    margin-bottom: 16px;

    padding-bottom: 12px;

    border-bottom: 1px solid $color-border-gray;

    h1 {

      margin: 0;

      font-size: 18px;

      font-weight: 600;

      color: $color-text-primary;

    }

  }

}

</style>

