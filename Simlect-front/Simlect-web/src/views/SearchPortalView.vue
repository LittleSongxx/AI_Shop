<template>
  <div class="search-portal simlect-search-portal">
    <header class="simlect-search-sticky">
      <div class="simlect-search-box">
        <el-input
          ref="inputRef"
          v-model="keyword"
          placeholder="搜索商品名称"
          clearable
          class="keyword-input"
          @keyup.enter="submitSearch"
        >
          <template #prefix>
            <el-icon class="input-icon"><Search /></el-icon>
          </template>
        </el-input>
        <button type="button" class="simlect-search-btn" @click="submitSearch()">搜索</button>
      </div>
    </header>

    <div class="simlect-search-body">
      <section v-if="guessWords.length" class="section">
        <div class="section-head">
          <h3 class="section-title">猜你想搜</h3>
        </div>
        <div class="tag-cloud">
          <button
            v-for="(word, index) in guessWords"
            :key="`guess-${index}`"
            type="button"
            class="tag-chip"
            @click="searchByWord(word)"
          >
            {{ word }}
          </button>
        </div>
      </section>

      <section v-if="hotWords.length" class="section">
        <div class="section-head">
          <h3 class="section-title">大家都在搜</h3>
        </div>
        <div class="tag-cloud">
          <button
            v-for="(word, index) in hotWords"
            :key="`hot-${index}`"
            type="button"
            class="tag-chip hot"
            @click="searchByWord(word)"
          >
            <span v-if="index < 3" class="rank">{{ index + 1 }}</span>
            {{ word }}
          </button>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <h3 class="section-title">最近搜索</h3>
          <button
            v-if="recentWords.length"
            type="button"
            class="clear-btn"
            @click="clearRecent"
          >
            清空最近搜索记录
          </button>
        </div>
        <div v-if="recentWords.length" class="tag-cloud">
          <button
            v-for="word in recentWords"
            :key="`recent-${word}`"
            type="button"
            class="tag-chip recent"
            @click="searchByWord(word)"
          >
            {{ word }}
            <el-icon class="remove-icon" @click.stop="removeRecent(word)"><Close /></el-icon>
          </button>
        </div>
        <p v-else class="empty-tip">暂无搜索记录</p>
      </section>

      <section v-if="recommendProducts.length" class="section">
        <div class="section-head">
          <h3 class="section-title">推荐精选</h3>
        </div>
        <div class="recommend-grid">
          <RouterLink
            v-for="p in recommendProducts"
            :key="p.productId"
            class="recommend-item"
            :to="`/product/${p.productId}`"
          >
            <img :src="coverOf(p)" alt="" class="recommend-cover" />
            <p class="recommend-name">{{ p.productName }}</p>
            <p class="recommend-price">¥{{ p.minPrice }}</p>
          </RouterLink>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { Close, Search } from '@element-plus/icons-vue';
import { confirmAction } from '@/utils/confirm';
import { useSearchStore } from '@/stores/search';
import { searchApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { pickProductCover, resolveImageUrl } from '@/utils/image';
import { DEFAULT_HOT_SEARCH_WORDS } from '@/constants/searchHotWords';

const router = useRouter();
const searchStore = useSearchStore();
const authStore = useAuthStore();
const keyword = ref('');
const hotWords = ref<string[]>([]);
const guessWords = ref<string[]>([]);
const recentWords = ref<string[]>([]);
const recommendProducts = ref<any[]>([]);
const inputRef = ref();

const refreshRecent = async () => {
  if (!authStore.isLoggedIn) {
    recentWords.value = [];
    return;
  }
  try {
    const list = await searchApi.loadRecentKeywords();
    recentWords.value = Array.isArray(list) ? list.slice(0, 10) : [];
  } catch {
    recentWords.value = [];
  }
};

const submitSearch = async (word?: string) => {
  const keyWords = (word ?? keyword.value).trim();
  if (!keyWords) return;
  if (authStore.isLoggedIn) {
    try {
      await searchApi.saveKeyword(keyWords);
    } catch {

    }
  }
  searchStore.setSearch({ keyWords, categoryId: '' });
  router.push({ path: '/search-result', query: { q: keyWords } });
};

const searchByWord = (word: string) => {
  keyword.value = word;
  submitSearch(word);
};

const clearRecent = async () => {
  const ok = await confirmAction('确定要清空全部最近搜索记录吗？', {
    title: '清空记录',
    confirmButtonText: '清空'
  });
  if (!ok) return;
  if (authStore.isLoggedIn) {
    await searchApi.clearRecentKeywords();
  }
  await refreshRecent();
};

const removeRecent = async (word: string) => {
  if (authStore.isLoggedIn) {
    await searchApi.removeRecentKeyword(word);
  }
  await refreshRecent();
};

const coverOf = (p: Record<string, unknown>) =>
  resolveImageUrl(pickProductCover(p), { useThumbnail: true });

onMounted(async () => {
  try {
    const hot = await searchApi.loadHotKeywords();
    hotWords.value = Array.isArray(hot) && hot.length ? hot : [...DEFAULT_HOT_SEARCH_WORDS];
  } catch {
    hotWords.value = [...DEFAULT_HOT_SEARCH_WORDS];
  }
  await refreshRecent();
  if (authStore.isLoggedIn) {
    try {
      guessWords.value = (await searchApi.loadGuessKeywords()) || [];
      recommendProducts.value = (await searchApi.loadRecommendProducts(6)) || [];
    } catch {
      guessWords.value = [];
      recommendProducts.value = [];
    }
  }
  inputRef.value?.focus?.();
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.section {
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-title;
}

.clear-btn {
  border: none;
  background: none;
  color: $color-text-muted;
  font-size: 13px;
  cursor: pointer;
  transition: color $transition-fast;

  &:hover {
    color: $color-primary;
  }
}

.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag-chip {
  border: 1px solid $color-border;
  background: $color-bg-subtle;
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 13px;
  color: $color-text-body;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition:
    border-color $transition-fast,
    color $transition-fast,
    background $transition-fast;

  &:hover {
    border-color: $color-primary;
    color: $color-primary;
    background: $color-primary-soft;
  }

  &.hot .rank {
    color: $color-gold;
    font-weight: 700;
    margin-right: 2px;
  }
}

.remove-icon {
  font-size: 12px;
  opacity: 0.6;
  transition: opacity $transition-fast;

  &:hover {
    opacity: 1;
  }
}

.empty-tip {
  color: $color-text-muted;
  font-size: 13px;
  margin: 0;
}

.recommend-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.recommend-item {
  text-decoration: none;
  color: inherit;
  border-radius: $radius-sm;
  overflow: hidden;
  background: $color-bg-subtle;
  transition: transform 0.4s cubic-bezier(0.25, 0.1, 0.25, 1), box-shadow 0.4s cubic-bezier(0.25, 0.1, 0.25, 1);

  &:hover {
    transform: translateY(-3px);
    box-shadow: $shadow-card-hover;
  }
}

.recommend-cover {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
}

.recommend-name {
  margin: 6px 8px 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recommend-price {
  margin: 4px 8px 8px;
  color: $color-primary;
  font-weight: 600;
  font-size: 14px;
}
</style>
