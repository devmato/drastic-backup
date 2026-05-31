import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'
import { disconnectAppSocket } from 'src/utils/socket'
import { isAuthError, isRecoveryKeyError } from 'src/utils/auth'

export const useUserStore = defineStore('user', () => {

  const user = ref(null)
  const recoveryKey = ref(null)
  const needsInit = ref(false)
  const environment = ref('dev')
  const reauthDialogOpen = ref(false)
  const reauthSubmitting = ref(false)
  let reauthResolve = null
  let reauthReject = null
  let reauthPromise = null

  const loggedIn = computed(() => {
    return user.value ? true : false
  })

  const hasRecoveryKey = computed(() => Boolean(recoveryKey.value))

  async function login(username, password) {
    const response = await api.post('/auth/login', { username, password })
    recoveryKey.value = response.data.recovery_key || null
    return response.data
  }

  async function refreshSession() {
    await api.post('/auth/refresh', null, { _skipAuthRefresh: true })
    return true
  }

  async function fetchNeedsInit() {
    const response = await api.get('/user/needs_init')
    needsInit.value = Boolean(response.data.needs_init)
    return needsInit.value
  }

  async function fetchAuthStatus() {
    const response = await api.get('/auth/status', { _skipAuthRefresh: true })
    environment.value = response.data.environment || 'dev'
    user.value = response.data.authenticated ? response.data.user : null
    if (!response.data.authenticated) {
      recoveryKey.value = null
    }
    return response.data
  }

  async function initializeUser(username, password) {
    const response = await api.post('/user/init', { username, password })
    needsInit.value = false
    return response.data
  }

  async function changePassword(currentPassword, newPassword) {
    const response = await api.put('/user/password', {
      current_password: currentPassword,
      new_password: newPassword,
    })
    clearSessionState()
    return response.data
  }

  function clearReauthRequest() {
    reauthResolve = null
    reauthReject = null
    reauthPromise = null
  }

  function requestReauth() {
    if (reauthPromise) {
      return reauthPromise
    }

    reauthDialogOpen.value = true
    reauthPromise = new Promise((resolve, reject) => {
      reauthResolve = resolve
      reauthReject = reject
    })
    return reauthPromise
  }

  async function confirmReauth(password) {
    const username = user.value?.name
    if (!username) {
      throw new Error('No signed-in user found')
    }

    reauthSubmitting.value = true
    try {
      await login(username, password)
      await fetchAuthStatus()
      if (!recoveryKey.value) {
        throw new Error('Recovery key was not returned')
      }

      reauthResolve?.(recoveryKey.value)
      reauthDialogOpen.value = false
      clearReauthRequest()
      return recoveryKey.value
    } finally {
      reauthSubmitting.value = false
    }
  }

  function cancelReauth() {
    const error = new Error('Reauthentication cancelled')
    error.__drasticReauthCancelled = true
    reauthReject?.(error)
    reauthDialogOpen.value = false
    reauthSubmitting.value = false
    clearReauthRequest()
  }

  async function ensureRecoveryKey(force = false) {
    if (recoveryKey.value && !force) {
      return recoveryKey.value
    }

    return requestReauth()
  }

  async function withRecoveryKey(operation, { require = false } = {}) {
    let key = require ? await ensureRecoveryKey() : recoveryKey.value

    try {
      return await operation(key)
    } catch (error) {
      if (!isRecoveryKeyError(error)) {
        throw error
      }

      recoveryKey.value = null
      key = await ensureRecoveryKey(true)
      return operation(key)
    }
  }

  function clearSessionState() {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('access_token')
    }
    disconnectAppSocket()
    user.value = null
    recoveryKey.value = null
    if (reauthPromise) {
      cancelReauth()
    }
  }

  async function logout() {
    try {
      await api.post('/auth/logout', null, {
        validateStatus: (status) => (status >= 200 && status < 300) || [401, 422].includes(status)
      })
    } finally {
      clearSessionState()
    }
  }

  async function getUser() {
    try {
      const status = await fetchAuthStatus()
      if (!status.authenticated) {
        return null
      }
      return user.value
    } catch (error) {
      if (isAuthError(error) || error?.response?.status === 422) {
        clearSessionState()
        return null
      }

      throw error
    }
  }

  return {
    user,
    recoveryKey,
    needsInit,
    environment,
    reauthDialogOpen,
    reauthSubmitting,
    loggedIn,
    hasRecoveryKey,
    login,
    refreshSession,
    fetchNeedsInit,
    fetchAuthStatus,
    initializeUser,
    changePassword,
    requestReauth,
    confirmReauth,
    cancelReauth,
    ensureRecoveryKey,
    withRecoveryKey,
    clearSessionState,
    logout,
    getUser
  }
})
