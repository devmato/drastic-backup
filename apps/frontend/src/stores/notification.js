import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as notificationApi from 'src/api/notifications'

export const useNotificationStore = defineStore('notification', () => {
  const configs = ref([])
  const options = ref({ operation_types: [], operation_states: [] })
  const loading = ref(false)

  async function loadConfigs() {
    loading.value = true
    try {
      configs.value = await notificationApi.listConfigs()
    } finally {
      loading.value = false
    }
  }

  async function loadOptions() {
    options.value = await notificationApi.getOptions()
  }

  async function createConfig(data) {
    await notificationApi.createConfig(data)
    await loadConfigs()
  }

  async function updateConfig(configId, data) {
    await notificationApi.updateConfig(configId, data)
    await loadConfigs()
  }

  async function deleteConfig(configId) {
    await notificationApi.deleteConfig(configId)
    await loadConfigs()
  }

  return { configs, options, loading, loadConfigs, loadOptions, createConfig, updateConfig, deleteConfig,
    testNotification: notificationApi.testNotification }
})
