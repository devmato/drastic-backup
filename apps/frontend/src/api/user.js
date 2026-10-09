import { api } from 'boot/axios'

export async function login(username, password) {
  return (await api.post('/auth/login', { username, password })).data
}

export async function refreshSession() {
  await api.post('/auth/refresh', null, { _skipAuthRefresh: true })
}

export async function getInitializationStatus() {
  return (await api.get('/user/needs_init')).data
}

export async function getAuthStatus() {
  return (await api.get('/auth/status', { _skipAuthRefresh: true })).data
}

export async function initializeUser(username, password) {
  return (await api.post('/user/init', { username, password })).data
}

export async function changePassword(currentPassword, newPassword) {
  return (await api.put('/user/password', { current_password: currentPassword, new_password: newPassword })).data
}

export async function getDebugAccess() {
  return (await api.get('/user/debug')).data
}

export async function setDebugAccess(enabled) {
  return (await api[enabled ? 'post' : 'delete']('/user/debug')).data
}

export async function logout() {
  await api.post('/auth/logout', null, {
    validateStatus: status => (status >= 200 && status < 300) || [401, 422].includes(status),
  })
}

export async function recoveryExport(password) {
  // This endpoint needs both the blob and Content-Disposition, unlike JSON endpoints.
  try {
    return await api.post('/user/recovery-export', { password }, { responseType: 'blob' })
  } catch (error) {
    const data = error?.response?.data
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      try {
        const text = await data.text()
        if (text.trim()) {
          try {
            error.response.data = JSON.parse(text)
          } catch {
            error.response.data = { msg: text }
          }
        }
      } catch {
        // Preserve the original error when the response body cannot be read.
      }
    }
    throw error
  }
}
