<template>
  <div class="home-dashboard">
    <TodayData />
    <WeeklyData />
    <SaleData />
  </div>
</template>

<script setup>
import SaleData from './SaleData.vue'
import TodayData from './TodayData.vue'
import WeeklyData from './WeeklyData.vue'
</script>

<style lang="scss" scoped>
.home-dashboard {
  display: grid;
  grid-template-rows: auto minmax(230px, 0.9fr) minmax(240px, 1.1fr);
  gap: 12px;
  width: 100%;
  min-height: 100%;
  max-width: 100%;
}

@media (max-width: 1200px) {
  .home-dashboard {
    display: flex;
    flex-direction: column;
    min-height: auto;
  }
}
</style>
