<template>
  <div class="address-page">
    <div class="page-toolbar">
      <p class="toolbar-tip">
        {{ isSelectMode ? '点击地址即可选中并返回确认订单' : '管理你的收货地址，下单时可直接选用' }}
      </p>
      <el-button type="primary" round @click="openForm()">
        <el-icon><Plus /></el-icon>
        新增地址
      </el-button>
    </div>

    <div v-if="list.length" class="address-list">
      <template v-for="item in list" :key="item.addressId">
        <SwipeActionsRow
          v-if="useSwipeActions"
          :open="openSwipeId === item.addressId"
          :action-width="152"
          @open="openSwipeId = item.addressId"
          @close="onSwipeClose(item.addressId)"
        >
          <article
            class="address-card card-flat"
            :class="cardClass(item)"
            @click="onCardClick(item)"
          >
            <AddressCardBody :item="item" />
          </article>
          <template #actions>
            <button type="button" class="swipe-act edit" @click.stop="openForm(item)">编辑</button>
            <button type="button" class="swipe-act delete" @click.stop="remove(item.addressId)">
              删除
            </button>
          </template>
        </SwipeActionsRow>

        <article
          v-else
          class="address-card card-flat"
          :class="cardClass(item)"
          @click="onCardClick(item)"
        >
          <AddressCardBody :item="item" />
          <div v-if="!isSelectMode" class="card-actions">
            <el-button link type="primary" @click.stop="openForm(item)">编辑</el-button>
            <el-button
              v-if="item.defaultType !== 1"
              link
              @click.stop="setDefault(item.addressId)"
            >
              设为默认
            </el-button>
            <el-button link type="danger" @click.stop="remove(item.addressId)">删除</el-button>
          </div>
          <p v-else class="select-hint">点击使用此地址</p>
        </article>
      </template>
    </div>

    <el-empty v-else description="还没有收货地址" class="address-empty">
      <el-button type="primary" round @click="openForm()">添加收货地址</el-button>
    </el-empty>

    <AddressFormPanel v-model="formVisible" :edit-item="editingItem" @saved="onFormSaved" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Plus } from '@element-plus/icons-vue';
import AddressCardBody from '@/components/business/AddressCardBody.vue';
import AddressFormPanel, { type AddressFormItem } from '@/components/business/AddressFormPanel.vue';
import SwipeActionsRow from '@/components/business/SwipeActionsRow.vue';
import { addressApi } from '@/api/modules';
import { useDevice } from '@/composables/useDevice';
import { confirmAction } from '@/utils/confirm';
import { saveCheckoutSelectedAddress, loadCheckoutSelectedAddress } from '@/utils/checkout';
import { toast } from '@/utils/toast';
import { usePageRefresh } from '@/composables/pullRefresh';

const route = useRoute();
const router = useRouter();
const { isDesktop } = useDevice();
const isSelectMode = computed(() => route.query.from === 'checkout');
const useSwipeActions = computed(() => !isDesktop.value && !isSelectMode.value);
const pickedAddressId = ref(loadCheckoutSelectedAddress() || '');

const list = ref<AddressFormItem[]>([]);
const formVisible = ref(false);
const editingItem = ref<AddressFormItem | null>(null);
const openSwipeId = ref('');

const sortList = (rows: AddressFormItem[]) =>
  [...rows].sort((a, b) => (b.defaultType === 1 ? 1 : 0) - (a.defaultType === 1 ? 1 : 0));

const cardClass = (item: AddressFormItem) => ({
  'is-default': item.defaultType === 1,
  'is-selectable': isSelectMode.value,
  'is-picked': isSelectMode.value && pickedAddressId.value === item.addressId
});

const load = async () => {
  const data = await addressApi.loadDataList();
  list.value = sortList(Array.isArray(data) ? data : []);
};

const openForm = (item?: AddressFormItem) => {
  editingItem.value = item ?? null;
  formVisible.value = true;
};

const onFormSaved = async () => {
  const wasAdd = !editingItem.value;
  const prevIds = new Set(list.value.map((a) => a.addressId));
  editingItem.value = null;
  await load();
  if (isSelectMode.value && wasAdd) {
    const added = list.value.find((a) => !prevIds.has(a.addressId));
    if (added) {
      selectForCheckout(added);
    }
  }
};

const remove = async (addressId: string) => {
  const ok = await confirmAction('删除后无法恢复，确定要删除该收货地址吗？', {
    title: '删除地址',
    confirmButtonText: '删除'
  });
  if (!ok) return;
  await addressApi.delAddress(addressId);
  toast.success('已删除');
  if (openSwipeId.value === addressId) openSwipeId.value = '';
  await load();
};

const setDefault = async (addressId: string) => {
  await addressApi.updateDefault(addressId);
  toast.success('已设为默认地址');
  await load();
};

const selectForCheckout = (item: AddressFormItem) => {
  saveCheckoutSelectedAddress(item.addressId);
  router.push('/checkout');
};

const onCardClick = (item: AddressFormItem) => {
  if (isSelectMode.value) selectForCheckout(item);
};

const onSwipeClose = (id: string) => {
  if (openSwipeId.value === id) openSwipeId.value = '';
};

const bootstrap = async () => {
  await load();
  if (isSelectMode.value && route.query.action === 'add') {
    openForm();
  }
};

onMounted(bootstrap);
usePageRefresh(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.address-page {
  padding-bottom: calc(16px + env(safe-area-inset-bottom, 0));
  min-height: min(100%, calc(100dvh - 120px));
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;

  .toolbar-tip {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
    flex: 1;
    min-width: 0;
  }
}

.address-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.address-card {
  padding: 14px 14px 10px;
  border: 1px solid $color-border;
  transition: border-color $transition-fast, box-shadow $transition-fast;

  &.is-default {
    border-color: rgba($color-primary, 0.45);
    background: linear-gradient(180deg, #fffaf7 0%, #fff 40%);
  }

  &.is-selectable {
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;

    &:active {
      transform: scale(0.995);
    }

    &.is-picked {
      border-color: rgba($color-primary, 0.55);
      box-shadow: 0 0 0 1px rgba($color-primary, 0.15);
    }
  }

  .select-hint {
    margin: 10px 0 0;
    padding-top: 10px;
    border-top: 1px dashed $color-border;
    font-size: 12px;
    color: $color-primary;
    text-align: center;
  }

  .card-actions {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px dashed $color-border;
  }
}

.swipe-act {
  flex: 1;
  border: none;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;

  &.edit {
    background: $color-primary;
  }

  &.delete {
    background: #e74c3c;
  }
}

.address-empty {
  padding: 40px 0;
}
</style>
