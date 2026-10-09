import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as jobs from 'src/api/jobs'

export const useJobStore = defineStore('job', () => {
  const agentJobs = ref([])
  const loading = ref(false)

  async function loadJobs() {
    loading.value = true
    try {
      agentJobs.value = await jobs.listJobs()
    } finally {
      loading.value = false
    }
  }

  async function deleteJob(id) {
    await jobs.deleteJob(id)
    await loadJobs()
  }

  async function cancelJob(id) {
    await jobs.cancelJob(id)
    await loadJobs()
  }

  return {
    agentJobs, loading, loadJobs, deleteJob, cancelJob,
    getJob: jobs.getJob, getDirlist: jobs.getDirlist,
    getProxmoxGuests: jobs.getProxmoxGuests, getContainers: jobs.getContainers,
  }
})
