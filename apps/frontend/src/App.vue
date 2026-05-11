<template>
  <router-view/>
</template>

<script setup>
import { onBeforeMount } from 'vue'
import { api } from 'boot/axios'
import { useQuasar } from 'quasar'

const $q = useQuasar()

onBeforeMount(() => {
  api.interceptors.response.use(function(response) {
    return response
  }, function(error) {
    if (error.response && error.response.status == 500) {
      $q.notify({
        message: 'An error occurred',
        position: 'top',
        color: 'red',
        actions: [
          { label: 'Reload page', color: 'white', handler: () => { window.location.reload() } }
        ]
      })
    }

    return Promise.reject(error)
  })
})

defineOptions({
  name: 'App'
})
</script>
