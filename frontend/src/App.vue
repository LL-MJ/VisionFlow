<script setup lang="ts">
import { onMounted, ref } from 'vue'

const status = ref('正在检查后端连接…')

onMounted(async () => {
  try {
    const response = await fetch('/health')
    if (!response.ok || (await response.json()).status !== 'ok') throw new Error('health check failed')
    status.value = '后端已连接'
  } catch {
    status.value = '后端未连接，请检查后端是否启动。'
  }
})
</script>

<template>
  <main>
    <h1>VisionFlow</h1>
    <p>{{ status }}</p>
  </main>
</template>
