import { api } from 'boot/axios'

export async function listConfigs() {
  return (await api.get('/notifications/')).data
}

export async function getOptions() {
  return (await api.get('/notifications/options')).data
}

export async function createConfig(data) {
  await api.post('/notifications/', data)
}

export async function updateConfig(id, data) {
  await api.put(`/notifications/${id}`, data)
}

export async function deleteConfig(id) {
  await api.delete(`/notifications/${id}`)
}

export async function testNotification(url) {
  return (await api.post('/notifications/test', { url })).data
}
