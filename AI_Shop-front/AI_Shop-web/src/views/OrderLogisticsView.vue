<template>
  <div class="order-logistics-page card" v-loading="loading">
    <section v-if="logistics" class="logistics-section">
      <h3 class="section-title">物流信息</h3>

      <div class="logistics-head">
        <div class="company-line">
          <span class="company">{{ logistics.logisticsCompany || '物流公司' }}</span>
          <span class="no">{{ logistics.logisticsNo }}</span>
        </div>
        <el-tag type="info" effect="plain" size="small">{{ logistics.logisticsStatusName }}</el-tag>
      </div>

      <div class="address-card">
        <div class="addr-row">
          <span class="label">发件</span>
          <span class="text">
            {{ logistics.senderName }} {{ logistics.senderPhone }}
            <br />
            {{ logistics.senderAddress }}
          </span>
        </div>
        <div class="addr-row">
          <span class="label">收件</span>
          <span class="text">
            {{ logistics.receiverName }} {{ logistics.receiverPhone }}
            <br />
            {{ logistics.receiverAddress }}
          </span>
        </div>
      </div>

      <el-timeline v-if="recordList.length" class="track-timeline">
        <el-timeline-item
          v-for="(item, index) in recordList"
          :key="item.recordId"
          placement="top"
          :hide-timestamp="true"
        >
          <div class="track-head">
            <span v-if="index === 0" class="status-name">{{ logistics.logisticsStatusName }}</span>
            <span class="track-time">{{ item.recordTime }}</span>
          </div>
          <p class="track-address">{{ item.recordAddress }}</p>
        </el-timeline-item>
      </el-timeline>
    </section>

    <el-empty v-else-if="!loading" description="暂无物流信息" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { orderApi } from '@/api/modules';

const route = useRoute();
const loading = ref(true);
const logistics = ref<Record<string, any> | null>(null);

const recordList = computed(() => {
  const list = logistics.value?.recordList;
  return Array.isArray(list) ? list : [];
});

const load = async () => {
  loading.value = true;
  try {
    logistics.value = (await orderApi.getLogistics(String(route.params.orderId))) || null;
  } finally {
    loading.value = false;
  }
};

onMounted(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.order-logistics-page {
  padding: 14px 16px;
}

.section-title {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
}

.logistics-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid $color-border;

  .company-line {
    font-size: 14px;
    color: $color-text-title;

    .company {
      font-weight: 600;
      margin-right: 8px;
    }

    .no {
      color: $color-text-body;
    }
  }
}

.address-card {
  margin-bottom: 16px;
  padding: 10px 12px;
  background: #fafafa;
  border-radius: $radius-xs;
  font-size: 12px;
  line-height: 1.5;

  .addr-row {
    display: flex;
    gap: 8px;

    & + .addr-row {
      margin-top: 8px;
    }

    .label {
      flex-shrink: 0;
      color: $color-text-muted;
    }

    .text {
      color: $color-text-body;
    }
  }
}

.track-timeline {
  padding-left: 4px;

  .track-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 13px;

    .status-name {
      color: $color-primary;
      font-weight: 600;
    }

    .track-time {
      color: $color-text-muted;
      font-size: 12px;
    }
  }

  .track-address {
    margin: 6px 0 0;
    font-size: 12px;
    color: $color-text-body;
    line-height: 1.5;
  }
}
</style>
