<template>
  <section class="ops-section">
    <header class="section-head">
      <h3 class="section-title">运营待办</h3>
    </header>

    <div class="ops-layout">
      <div class="ops-panel card">
        <div class="panel-head">
          <span class="panel-title">待发货</span>
        </div>
        <div class="panel-body">
          <ShippedOrder />
        </div>
      </div>

      <div class="ops-panel card">
        <div class="panel-head">
          <span class="panel-title">库存预警</span>
        </div>
        <div class="panel-body">
          <LessStockData />
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import LessStockData from './LessStockData.vue'
import ShippedOrder from './ShippedOrder.vue'
</script>

<style lang="scss" scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--header-border);
  box-shadow: var(--shadow-card);
  border-radius: 12px;
}

.ops-section {
  margin-top: 10px;
}

.section-head {
  margin-bottom: 8px;

  .section-title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
  }
}

.ops-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
  gap: 10px;
}

.ops-panel {
  display: flex;
  flex-direction: column;
  height: 196px;
  overflow: hidden;

  .panel-head {
    flex-shrink: 0;
    padding: 8px 12px;
    border-bottom: 1px solid var(--header-border);
  }

  .panel-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text2);
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 4px 8px 6px;

    :deep(.table-height) {
      height: 100%;
    }

    :deep(.el-table) {
      font-size: 12px;
    }

    :deep(.el-table .el-table__cell) {
      padding: 6px 0;
    }

    :deep(.pagination) {
      display: none;
    }
  }
}

@media (max-width: 960px) {
  .ops-layout {
    grid-template-columns: 1fr;
  }
}
</style>
