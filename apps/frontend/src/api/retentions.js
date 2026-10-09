import { api } from 'boot/axios'

export async function listRetentions() {
  return (await api.get('/retentions/')).data
}

export async function createRetention(data) {
  await api.post('/retentions/', data)
}

export async function updateRetention(id, data) {
  await api.put(`/retentions/${id}`, data)
}

export async function deleteRetention(id) {
  await api.delete(`/retentions/${id}`)
}
