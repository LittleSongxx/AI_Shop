<template>
  <div class="card">
    <h2>{{ title }}</h2>
    <p>{{ desc }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; desc: string }>();
</script>
