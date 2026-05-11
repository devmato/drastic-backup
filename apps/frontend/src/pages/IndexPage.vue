<template>
  <q-page class="flex flex-center">
    <div class="text-h6">Welcome to dRastic Backup</div>
  </q-page>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { onBeforeMount } from 'vue'

const router = useRouter()

onBeforeMount(() => {
  router.push('/agents')
})

defineOptions({ name: 'IndexPage' });
</script>
