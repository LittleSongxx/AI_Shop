<template>
  <div class="profile-page">
    <section class="profile-intro">
      <p class="eyebrow">AI 导购</p>
      <h1>购物偏好</h1>
      <p>只保存你明确表达的稳定偏好。当前会话中的需求始终优先于这里的设置。</p>
      <span class="revision">版本 {{ profile.revision }}</span>
    </section>

    <el-skeleton :loading="loading" animated :count="4">
      <template #default>
        <form class="profile-form" @submit.prevent="save">
          <section class="form-section">
            <h2>基本需求</h2>
            <div class="field-grid">
              <label class="field">
                <span>常买品类</span>
                <el-input v-model="profile.category" maxlength="40" clearable placeholder="例如：手机、家电" />
              </label>
              <label class="field">
                <span>预算范围（元）</span>
                <div class="budget-row">
                  <el-input-number v-model="profile.budgetMin" :min="0" :precision="2" :controls="false" placeholder="最低" />
                  <span>至</span>
                  <el-input-number v-model="profile.budgetMax" :min="0" :precision="2" :controls="false" placeholder="最高" />
                </div>
              </label>
              <label class="field">
                <span>是否接受替代品</span>
                <el-select v-model="substituteValue" clearable placeholder="未设置">
                  <el-option label="接受" value="true" />
                  <el-option label="不接受" value="false" />
                </el-select>
              </label>
            </div>
          </section>

          <section class="form-section">
            <h2>品牌偏好</h2>
            <div class="tag-editor">
              <div class="tag-list">
                <el-tag v-for="brand in profile.brands" :key="`brand-${brand}`" closable @close="removeTag('brands', brand)">
                  {{ brand }}
                </el-tag>
                <span v-if="!profile.brands.length" class="muted">暂未设置</span>
              </div>
              <div class="tag-input-row">
                <el-input v-model="brandInput" maxlength="30" placeholder="输入品牌后回车" @keyup.enter.prevent="addTag('brands')" />
                <el-button type="primary" plain @click="addTag('brands')">添加</el-button>
              </div>
            </div>
            <div class="tag-editor">
              <span class="field-label">排除品牌</span>
              <div class="tag-list">
                <el-tag v-for="brand in profile.excludedBrands" :key="`excluded-${brand}`" type="info" closable @close="removeTag('excludedBrands', brand)">
                  {{ brand }}
                </el-tag>
                <span v-if="!profile.excludedBrands.length" class="muted">暂未设置</span>
              </div>
              <div class="tag-input-row">
                <el-input v-model="excludedBrandInput" maxlength="30" placeholder="输入不考虑的品牌" @keyup.enter.prevent="addTag('excludedBrands')" />
                <el-button plain @click="addTag('excludedBrands')">添加</el-button>
              </div>
            </div>
          </section>

          <section class="form-section">
            <h2>场景与功能</h2>
            <div class="tag-editor">
              <span class="field-label">使用场景</span>
              <div class="tag-list">
                <el-tag v-for="item in profile.scenarios" :key="`scenario-${item}`" closable @close="removeTag('scenarios', item)">{{ item }}</el-tag>
                <span v-if="!profile.scenarios.length" class="muted">暂未设置</span>
              </div>
              <div class="tag-input-row">
                <el-input v-model="scenarioInput" maxlength="40" placeholder="例如：通勤、旅行" @keyup.enter.prevent="addTag('scenarios')" />
                <el-button plain @click="addTag('scenarios')">添加</el-button>
              </div>
            </div>
            <div class="tag-editor">
              <span class="field-label">功能偏好</span>
              <div class="tag-list">
                <el-tag v-for="item in profile.features" :key="`feature-${item}`" closable @close="removeTag('features', item)">{{ item }}</el-tag>
                <span v-if="!profile.features.length" class="muted">暂未设置</span>
              </div>
              <div class="tag-input-row">
                <el-input v-model="featureInput" maxlength="40" placeholder="例如：降噪、续航长" @keyup.enter.prevent="addTag('features')" />
                <el-button plain @click="addTag('features')">添加</el-button>
              </div>
            </div>
          </section>

          <footer class="form-actions">
            <el-button type="danger" plain :loading="saving" @click="clearAll">清空偏好</el-button>
            <span class="action-spacer" />
            <el-button :disabled="saving" @click="load">放弃修改</el-button>
            <el-button type="primary" :loading="saving" native-type="submit">保存偏好</el-button>
          </footer>
        </form>
      </template>
    </el-skeleton>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { agentApi, type ShoppingProfile } from '@/api/modules';
import { toast } from '@/utils/toast';

const emptyProfile = (): ShoppingProfile => ({
  revision: 0,
  category: '',
  budgetMin: null,
  budgetMax: null,
  brands: [],
  excludedBrands: [],
  scenarios: [],
  features: [],
  acceptSubstitute: null
});

const profile = reactive<ShoppingProfile>(emptyProfile());
const loading = ref(false);
const saving = ref(false);
const brandInput = ref('');
const excludedBrandInput = ref('');
const scenarioInput = ref('');
const featureInput = ref('');
const substituteValue = computed({
  get: () => profile.acceptSubstitute == null ? '' : String(profile.acceptSubstitute),
  set: (value: string) => { profile.acceptSubstitute = value === '' ? null : value === 'true'; }
});

const applyProfile = (value: Partial<ShoppingProfile> | null | undefined) => {
  const next = value || emptyProfile();
  Object.assign(profile, {
    ...emptyProfile(),
    ...next,
    brands: Array.isArray(next.brands) ? [...next.brands] : [],
    excludedBrands: Array.isArray(next.excludedBrands) ? [...next.excludedBrands] : [],
    scenarios: Array.isArray(next.scenarios) ? [...next.scenarios] : [],
    features: Array.isArray(next.features) ? [...next.features] : []
  });
};

const load = async () => {
  loading.value = true;
  try {
    applyProfile(await agentApi.getShoppingProfile());
  } catch (error: any) {
    toast.error(error?.info || '购物偏好加载失败');
  } finally {
    loading.value = false;
  }
};

const addTag = (field: 'brands' | 'excludedBrands' | 'scenarios' | 'features') => {
  const input = field === 'brands' ? brandInput : field === 'excludedBrands' ? excludedBrandInput : field === 'scenarios' ? scenarioInput : featureInput;
  const value = input.value.trim();
  if (!value) return;
  const list = profile[field] as string[];
  if (!list.includes(value)) list.push(value);
  input.value = '';
};

const removeTag = (field: 'brands' | 'excludedBrands' | 'scenarios' | 'features', value: string) => {
  const list = profile[field] as string[];
  const index = list.indexOf(value);
  if (index >= 0) list.splice(index, 1);
};

const payload = () => ({
  category: profile.category?.trim() || null,
  budgetMin: profile.budgetMin == null ? null : Number(profile.budgetMin),
  budgetMax: profile.budgetMax == null ? null : Number(profile.budgetMax),
  brands: [...profile.brands],
  excludedBrands: [...profile.excludedBrands],
  scenarios: [...profile.scenarios],
  features: [...profile.features],
  acceptSubstitute: profile.acceptSubstitute
});

const save = async () => {
  if (profile.budgetMin != null && profile.budgetMax != null && Number(profile.budgetMin) > Number(profile.budgetMax)) {
    toast.warning('最低预算不能高于最高预算');
    return;
  }
  saving.value = true;
  try {
    applyProfile(await agentApi.updateShoppingProfile(profile.revision, payload()));
    toast.success('购物偏好已保存');
  } catch (error: any) {
    if (Number(error?.code) === 409 && error?.data) {
      applyProfile(error.data);
      toast.warning('偏好已在其他设备更新，请基于最新版本重试');
    } else {
      toast.error(error?.info || '保存失败，请稍后重试');
    }
  } finally {
    saving.value = false;
  }
};

const clearAll = async () => {
  saving.value = true;
  try {
    applyProfile(await agentApi.clearShoppingProfile(profile.revision));
    toast.success('购物偏好已清空');
  } catch (error: any) {
    if (Number(error?.code) === 409 && error?.data) {
      applyProfile(error.data);
      toast.warning('偏好已在其他设备更新，请基于最新版本重试');
    } else {
      toast.error(error?.info || '清空失败，请稍后重试');
    }
  } finally {
    saving.value = false;
  }
};

onMounted(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.profile-page { width: min(100%, 900px); margin: 0 auto; padding: 16px $app-page-gutter 32px; box-sizing: border-box; }
.profile-intro { position: relative; padding: 8px 0 18px; }
.eyebrow { margin: 0; color: $color-primary; font-size: 12px; }
h1 { margin: 5px 0 7px; color: $color-text-title; font-size: 24px; }
.profile-intro p:not(.eyebrow) { max-width: 620px; margin: 0; color: $color-text-muted; font-size: 13px; line-height: 1.55; }
.revision { position: absolute; right: 0; bottom: 20px; color: $color-text-muted; font-size: 11px; }
.profile-form { display: flex; flex-direction: column; gap: 12px; }
.form-section { padding: 16px; border: 1px solid $color-border; border-radius: $radius-card; background: $color-card; }
.form-section h2 { margin: 0 0 14px; color: $color-text-title; font-size: 15px; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.field { display: flex; flex-direction: column; gap: 7px; min-width: 0; color: $color-text-body; font-size: 12px; }
.budget-row { display: flex; align-items: center; gap: 8px; }
.budget-row .el-input-number { flex: 1; min-width: 0; }
.tag-editor { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.field-label { color: $color-text-body; font-size: 12px; }
.tag-list { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; min-height: 24px; }
.muted { color: $color-text-muted; font-size: 12px; }
.tag-input-row { display: flex; gap: 8px; max-width: 440px; }
.tag-input-row .el-input { min-width: 0; }
.form-actions { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
.action-spacer { flex: 1; }
@media (max-width: 600px) {
  .profile-page { padding-top: 8px; }
  h1 { font-size: 21px; }
  .revision { position: static; display: inline-block; margin-top: 9px; }
  .field-grid { grid-template-columns: 1fr; }
  .form-section { padding: 13px; }
  .tag-input-row { max-width: none; }
  .form-actions { flex-wrap: wrap; }
  .action-spacer { display: none; }
}
</style>
