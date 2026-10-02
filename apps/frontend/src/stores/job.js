import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useJobStore = defineStore('job', () => {
  const agentJobs = ref([])
  const loading = ref(false)

  async function loadJobs() {
    loading.value = true
    try {
      const response = await api.get('/jobs/')
      agentJobs.value = response.data
    } finally {
      loading.value = false
    }
  }

  async function getJob(jobId) {
    const response = await api.get(`/jobs/${jobId}`)
    return response.data
  }

  async function createJob(data) {
    const response = await api.post('/jobs/', data)
    await loadJobs()
    return response.data
  }

  async function updateJob(jobId, data) {
    await api.put(`/jobs/${jobId}`, data)
    await loadJobs()
  }

  async function deleteJob(jobId) {
    await api.delete(`/jobs/${jobId}`)
    await loadJobs()
  }

  async function cancelJob(jobId) {
    await api.post(`/jobs/${jobId}/cancel`)
    await loadJobs()
  }

  async function getDirlist(agentId, baseDirectory = '/') {
    const response = await api.get(`/jobs/dirlist?agent_id=${agentId}&base_directory=${encodeURIComponent(baseDirectory)}`)
    return response.data
  }

  async function getProxmoxGuests(agentId) {
    const response = await api.get(`/jobs/proxmox-guests?agent_id=${agentId}`)
    return response.data.guests
  }

  // Schedules
  async function createSchedule(jobId, data) {
    await api.post(`/jobs/${jobId}/schedules`, data)
    await loadJobs()
  }

  async function updateSchedule(scheduleId, data) {
    await api.put(`/jobs/schedules/${scheduleId}`, data)
    await loadJobs()
  }

  async function deleteSchedule(scheduleId) {
    await api.delete(`/jobs/schedules/${scheduleId}`)
    await loadJobs()
  }

  // Actions
  async function createAction(jobId, data) {
    await api.post(`/jobs/${jobId}/actions`, data)
    await loadJobs()
  }

  async function updateAction(actionId, data) {
    await api.put(`/jobs/actions/${actionId}`, data)
    await loadJobs()
  }

  async function deleteAction(actionId) {
    await api.delete(`/jobs/actions/${actionId}`)
    await loadJobs()
  }

  // Containers
  async function getContainers(agentId) {
    const response = await api.get(`/jobs/containers?agent_id=${agentId}`)
    return response.data.containers
  }

  return {
    agentJobs, loading, loadJobs, getJob, createJob, updateJob, deleteJob,
    cancelJob, getDirlist, getProxmoxGuests,
    createSchedule, updateSchedule, deleteSchedule,
    createAction, updateAction, deleteAction,
    getContainers
  }
})
