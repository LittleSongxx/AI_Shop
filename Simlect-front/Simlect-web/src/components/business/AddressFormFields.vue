<template>
  <el-form
    label-position="top"
    class="address-form-fields"
    :class="{ 'is-drawer': embedded }"
    @submit.prevent="emit('submit')"
  >
    <el-form-item label="收货人" required>
      <el-input v-model="form.addressee" placeholder="请输入收货人姓名" maxlength="20" clearable />
    </el-form-item>
    <el-form-item label="手机号码" required>
      <el-input v-model="form.phone" placeholder="请输入 11 位手机号" maxlength="11" clearable />
    </el-form-item>
    <el-form-item label="所在地区" required>
      <div class="region-row">
        <el-cascader
          ref="regionCascaderRef"
          v-model="form.regionCodes"
          :options="regionOptions"
          :props="cascaderProps"
          placeholder="请选择省/市/区"
          clearable
          :filterable="isDesktop"
          teleported
          placement="bottom-start"
          :popper-class="regionPopperClass"
          class="region-cascader"
          @change="onRegionChange"
          @visible-change="onRegionPanelVisible"
        />
        <el-button
          type="primary"
          plain
          round
          size="small"
          class="btn-locate"
          :loading="locating"
          @click="applyCurrentLocation"
        >
          <el-icon><Location /></el-icon>
          当前位置
        </el-button>
      </div>
    </el-form-item>
    <el-form-item label="详细地址" required>
      <el-input
        v-model="form.detailAddress"
        type="textarea"
        :rows="2"
        maxlength="120"
        show-word-limit
        placeholder="街道、小区、门牌号等"
      />
    </el-form-item>
    <el-form-item class="form-item-default">
      <el-checkbox v-model="form.defaultType" :true-value="1" :false-value="0">
        设为默认地址
      </el-checkbox>
    </el-form-item>
    <div class="form-actions">
      <el-button type="primary" class="btn-save" round native-type="submit" :loading="saving">
        保存
      </el-button>
    </div>
  </el-form>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import type { CascaderInstance } from 'element-plus';
import { Location } from '@element-plus/icons-vue';
import { regionData } from 'element-china-area-data';
import { useDevice } from '@/composables/useDevice';
import { useUserLocationWeather } from '@/composables/useUserLocationWeather';
import { useAuthStore } from '@/stores/auth';
import { resolveRegionCodes } from '@/utils/regionGeocode';
import { toast } from '@/utils/toast';
import { useRouter } from 'vue-router';

const props = defineProps<{
  form: {
    addressee: string;
    phone: string;
    regionCodes: string[];
    detailAddress: string;
    defaultType: number;
  };
  saving?: boolean;

  embedded?: boolean;
}>();

const emit = defineEmits<{ submit: [] }>();

const { getBrowserPosition, fetchByCoords } = useUserLocationWeather();
const authStore = useAuthStore();
const router = useRouter();
const locating = ref(false);
const regionOptions = regionData;
const cascaderProps = {
  label: 'label',
  value: 'value',
  children: 'children',
  emitPath: true,

  checkStrictly: true
};

const parseStreetJson = (street: string): string => {
  try {
    const data = JSON.parse(street);
    const parts: string[] = [];
    if (data.street) parts.push(data.street);
    if (data.number) parts.push(data.number);
    return parts.join('');
  } catch {
    return street;
  }
};

const { isDesktop } = useDevice();
const regionCascaderRef = ref<CascaderInstance | null>(null);

const closeRegionCascader = () => {
  nextTick(() => {
    regionCascaderRef.value?.togglePopperVisible(false);
    const input = regionCascaderRef.value?.$el?.querySelector('input');
    if (input instanceof HTMLInputElement) input.blur();
  });
};

const findRegionLeaf = (codes: string[]) => {
  let level = regionOptions as { value: string; children?: unknown[] }[];
  let node: { value: string; children?: unknown[] } | undefined;
  for (const code of codes) {
    node = level.find((n) => String(n.value) === String(code));
    if (!node) return null;
    level = (node.children || []) as { value: string; children?: unknown[] }[];
  }
  return node ?? null;
};

const onRegionChange = (codes: string[]) => {
  if (!codes?.length) return;
  const leaf = findRegionLeaf(codes);
  if (leaf && !leaf.children?.length) closeRegionCascader();
};

const regionPopperClass = computed(() =>
  isDesktop.value ? 'address-region-popper' : 'address-region-popper address-region-popper--mobile'
);

const applyCurrentLocation = async () => {
  if (locating.value) return;
  if (!authStore.isLoggedIn) {
    toast.warning('请先登录后再获取当前位置');
    router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } });
    return;
  }
  locating.value = true;
  try {
    const pos = await getBrowserPosition();
    const { latitude, longitude } = pos.coords;

    const info = await fetchByCoords(latitude, longitude);

    const province = info?.province;
    const city = info?.city;
    const district = info?.district;
    const street = info?.street;

    if (!province && !city && !district) {
      toast.warning('未能解析当前位置');
      return;
    }

    const codes = resolveRegionCodes(province, city, district);
    if (codes?.length) {
      props.form.regionCodes = [...codes];
      await nextTick();
      const leaf = findRegionLeaf(codes);
      if (leaf && !leaf.children?.length) closeRegionCascader();
    } else {
      toast.warning('未能匹配到省市区，请手动选择');
    }
    if (street) {
      props.form.detailAddress = parseStreetJson(street);
    }
    if (codes?.length || street) {
      const needDistrict = codes?.length === 2;
      toast.success(
        needDistrict
          ? '已填入省市，请再选择区县'
          : codes?.length
            ? '已填入当前位置'
            : '已填入详细地址，请选择省市区'
      );
    }
  } catch (e: any) {
    const msg =
      e?.code === 1 ? '请允许浏览器获取位置权限' : e?.message || '定位失败';
    toast.warning(msg);
  } finally {
    locating.value = false;
  }
};

const patchMobileRegionTrigger = () => {
  if (isDesktop.value) return;
  nextTick(() => {
    const root = regionCascaderRef.value?.$el;
    if (!root) return;
    root.querySelectorAll('input').forEach((input: HTMLInputElement) => {
      input.setAttribute('readonly', 'true');
      input.setAttribute('inputmode', 'none');
      input.setAttribute('autocomplete', 'off');
    });
  });
};

const onRegionPanelVisible = (visible: boolean) => {
  if (!visible || isDesktop.value) return;
  patchMobileRegionTrigger();
  nextTick(() => {
    const root = regionCascaderRef.value?.$el;
    root?.querySelectorAll('input').forEach((input: HTMLInputElement) => input.blur());
  });
};

onMounted(patchMobileRegionTrigger);
watch(isDesktop, () => patchMobileRegionTrigger());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.address-form-fields {
  padding: 4px 4px 12px;

  :deep(.el-form-item) {
    margin-bottom: 16px;
  }

  :deep(.el-form-item__label) {
    font-size: 14px;
    font-weight: 500;
    color: $color-text-title;
    line-height: 1.4;
    padding-bottom: 6px;
  }

  :deep(.el-input__wrapper),
  :deep(.el-textarea__inner) {
    font-size: 15px;
  }

  .form-item-default {
    margin-bottom: 8px !important;
  }

  .region-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;

    .region-cascader {
      flex: 1;
      min-width: 0;
    }

    .btn-locate {
      align-self: flex-start;
    }
  }

  .region-cascader {
    width: 100%;

    :deep(.el-input__inner) {
      cursor: pointer;
    }
  }

  .form-actions {
    margin-top: 4px;
  }

  .btn-save {
    width: 100%;
    height: 44px;
    font-weight: 600;
  }

  &.is-drawer {
    display: flex;
    flex-direction: column;
    min-height: 100%;
    padding: 0;
    padding-bottom: 72px;

    :deep(.el-form-item) {
      margin-bottom: 14px;
    }

    .form-actions {
      position: fixed;
      left: 0;
      right: 0;
      bottom: $mobile-tab-height;
      z-index: 1000;
      margin-top: auto;
      margin: 0 12px;
      padding: 8px 16px;
      padding-bottom: calc(8px + env(safe-area-inset-bottom, 0));
    }

    .btn-save {
      margin-top: 0;
      box-shadow: 0 4px 12px rgba($color-primary, 0.25);
    }
  }
}
</style>
