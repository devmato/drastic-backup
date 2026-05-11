import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useNotificationStore = defineStore('notification', () => {
  const configs = ref([])
  const options = ref({ operation_types: [], operation_states: [] })
  const loading = ref(false)

  async function loadConfigs() {
    loading.value = true
    try {
      const response = await api.get('/notifications/')
      configs.value = response.data
    } finally {
      loading.value = false
    }
  }

  async function loadOptions() {
    const response = await api.get('/notifications/options')
    options.value = response.data
  }

  async function createConfig(data) {
    await api.post('/notifications/', data)
    await loadConfigs()
  }

  async function updateConfig(configId, data) {
    await api.put(`/notifications/${configId}`, data)
    await loadConfigs()
  }

  async function deleteConfig(configId) {
    await api.delete(`/notifications/${configId}`)
    await loadConfigs()
  }

  async function testNotification(url) {
    const response = await api.post('/notifications/test', { url })
    return response.data
  }

  return { configs, options, loading, loadConfigs, loadOptions, createConfig, updateConfig, deleteConfig, testNotification }
})
