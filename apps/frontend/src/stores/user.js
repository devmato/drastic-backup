import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import * as userApi from 'src/api/user'
import { disconnectAppSocket } from 'src/utils/socket'
import { isAuthError, isRecoveryKeyError } from 'src/utils/auth'
import { setDiagnosticRecording } from 'src/utils/diagnostics'

const RECOVERY_EXPORT_FALLBACK_FILENAME = 'drastic-backup-recovery-export.zip'

function getRecoveryExportFilename(contentDisposition) {
  const encodedFilename = contentDisposition?.match(/filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i)?.[1]
  const regularFilename = contentDisposition?.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i)
  let filename = encodedFilename || regularFilename?.[1] || regularFilename?.[2] || ''

  filename = filename.trim().replace(/^"|"$/g, '')
  if (encodedFilename) {
    try {
      filename = decodeURIComponent(filename)
    } catch {
      return RECOVERY_EXPORT_FALLBACK_FILENAME
    }
  }

  filename = filename
    .split(/[\\/]/)
    .pop()
    ?.replace(/[\u0000-\u001f\u007f<>:"|?*]/g, '_')
    .replace(/^\.+/, '')
    .trim()

  if (!filename) {
    return RECOVERY_EXPORT_FALLBACK_FILENAME
  }

  return filename.toLowerCase().endsWith('.zip') ? filename : `${filename}.zip`
}

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
    const data = await userApi.login(username, password)
    recoveryKey.value = data.recovery_key || null
    return data
  }

  async function refreshSession() {
    await userApi.refreshSession()
    return true
  }

  async function fetchNeedsInit() {
    const data = await userApi.getInitializationStatus()
    needsInit.value = Boolean(data.needs_init)
    return needsInit.value
  }

  async function fetchAuthStatus() {
    const data = await userApi.getAuthStatus()
    environment.value = data.environment || 'dev'
    user.value = data.authenticated ? data.user : null
    setDiagnosticRecording(user.value?.debug_enabled)
    if (!data.authenticated) {
      recoveryKey.value = null
    }
    return data
  }

  async function initializeUser(username, password) {
    const data = await userApi.initializeUser(username, password)
    needsInit.value = false
    return data
  }

  async function changePassword(currentPassword, newPassword) {
    const data = await userApi.changePassword(currentPassword, newPassword)
    clearSessionState()
    return data
  }

  async function getDebugAccess() {
    const data = await userApi.getDebugAccess()
    if (user.value) user.value.debug_enabled = data.enabled
    setDiagnosticRecording(data.enabled)
    return data
  }

  async function setDebugAccess(enabled) {
    const data = await userApi.setDebugAccess(enabled)
    if (user.value) user.value.debug_enabled = data.enabled
    setDiagnosticRecording(data.enabled)
    return data
  }

  async function downloadRecoveryExport(password) {
    const response = await userApi.recoveryExport(password)

    const filename = getRecoveryExportFilename(response.headers?.['content-disposition'])
    const objectUrl = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)

    try {
      link.click()
    } finally {
      link.remove()
      URL.revokeObjectURL(objectUrl)
    }
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
    setDiagnosticRecording(false)
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
      await userApi.logout()
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
    getDebugAccess,
    setDebugAccess,
    downloadRecoveryExport,
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
