<template>
  <div class="settings-page">
    <div class="form-panel card">
      <section class="form-section">
        <p class="section-title">头像</p>
        <div class="avatar-row">
          <UserAvatar :avatar="form.avatar" :size="72" />
          <div class="avatar-actions">
            <input
              ref="fileInputRef"
              type="file"
              accept="image/*"
              class="file-input"
              @change="onAvatarChange"
            />
            <el-button type="primary" plain round :loading="uploading" @click="pickAvatar">
              修改头像
            </el-button>
            <p class="hint">支持 JPG、PNG、GIF 等格式，选择后可裁剪区域</p>
          </div>
        </div>
      </section>

      <AvatarCropperDialog ref="cropperDialogRef" />

      <div class="section-divider" />

      <section class="form-section">
        <p class="section-title">昵称</p>
        <el-input v-model="form.nickName" placeholder="请输入昵称" maxlength="20" show-word-limit />
      </section>

      <div class="section-divider" />

      <section class="form-section">
        <p class="section-title">性别</p>
        <el-radio-group v-model="form.sex" class="sex-group">
          <el-radio :value="0">女</el-radio>
          <el-radio :value="1">男</el-radio>
          <el-radio :value="2">保密</el-radio>
        </el-radio-group>
      </section>

      <div class="save-row">
        <el-button type="primary" round class="save-btn" @click="saveInfo">保存资料</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { formatUploadErrorMessage } from '@/utils/apiError';
import UserAvatar from '@/components/common/UserAvatar.vue';
import AvatarCropperDialog from '@/components/business/AvatarCropperDialog.vue';
import { accountApi, fileApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const authStore = useAuthStore();
const uploading = ref(false);
const fileInputRef = ref<HTMLInputElement>();
const cropperDialogRef = ref<InstanceType<typeof AvatarCropperDialog>>();
const form = reactive<any>({ nickName: '', sex: 2, avatar: '' });

const load = async () => {
  Object.assign(form, await accountApi.getUserInfo());
};

const pickAvatar = () => {
  fileInputRef.value?.click();
};

const onAvatarChange = async (e: Event) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      ElMessage.warning('请选择图片文件');
      return;
    }
    if (file.size === 0) {
      ElMessage.warning('文件为空，请选择其他图片');
      return;
    }
    if (fileInputRef.value) fileInputRef.value.value = '';
    uploading.value = true;
    try {
      const blob = await cropperDialogRef.value!.open(file);
      if (!blob || blob.size === 0) {
        ElMessage.error('图片处理失败，尝试直接上传原始图片...');
        const uploaded = await fileApi.uploadImage(file, true, 'avatar');
        form.avatar = uploaded.path;
        await accountApi.updateUserInfo({ nickName: form.nickName, sex: form.sex, avatar: form.avatar });
        await authStore.fetchUserInfo();
        ElMessage.success('头像已更新');
        uploading.value = false;
        return;
      }
      const uploaded = await fileApi.uploadImage(blob, true, 'avatar');
      form.avatar = uploaded.path;
      await accountApi.updateUserInfo({ nickName: form.nickName, sex: form.sex, avatar: form.avatar });
      await authStore.fetchUserInfo();
      ElMessage.success('头像已更新');
    } catch (err) {
      console.error('头像上传失败:', err);
      ElMessage.error(formatUploadErrorMessage(err));
    } finally {
      uploading.value = false;
    }
  };

const saveInfo = async () => {
  await accountApi.updateUserInfo(form);
  await authStore.fetchUserInfo();
  ElMessage.success('资料已保存');
  router.push('/account/manage');
};

onMounted(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.settings-page {
  padding: 0 0 24px;
}

.form-panel {
  padding: 8px 16px 20px;
}

.form-section {
  padding: 12px 0;

  .section-title {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 500;
    color: $color-text-muted;
  }
}

.section-divider {
  height: 1px;
  background: $color-border;
}

.avatar-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.avatar-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;

  .hint {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.file-input {
  display: none;
}

.sex-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.save-row {
  display: flex;
  justify-content: center;
  padding: 20px 0 4px;

  .save-btn {
    min-width: 200px;
  }
}
</style>
