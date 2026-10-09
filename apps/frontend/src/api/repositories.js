import { api } from 'boot/axios'

export async function listRepositories() {
  return (await api.get('/repositories/')).data
}

export async function createRepository(data) {
  return (await api.post('/repositories/', data)).data
}

export async function updateRepository(id, data) {
  return (await api.put(`/repositories/${id}`, data)).data
}

export async function deleteRepository(id) {
  await api.delete(`/repositories/${id}`)
}

export async function revealRepositoryPassword(id, password) {
  return (await api.post(`/repositories/${id}/reveal-password`, { account_password: password })).data.password
}

export async function getUnlockAgents(id) {
  return (await api.get(`/repositories/${id}/unlock_agents`)).data
}
