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
  display: flex;
  flex-direction: column;
  gap: 0;
  max-width: 100%;
}
</style>
