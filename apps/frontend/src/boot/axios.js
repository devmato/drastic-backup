import { boot } from 'quasar/wrappers'
import axios from 'axios'
import { disconnectAppSocket } from 'src/utils/socket'
import { getCSRFToken } from 'src/utils/security'
import { canRefreshAuthError, isSessionAuthError, markAuthError } from 'src/utils/auth'

const api = axios.create({baseURL: '/api', withCredentials: true})
let authFailureInProgress = false
let authRefreshInFlight = null

function usesRefreshCookie(config) {
  return String(config?.url || '').endsWith('/auth/refresh')
}

function shouldSkipAuthRefresh(config) {
  if (!config) {
    return true
  }

  const url = String(config.url || '')
  return Boolean(
    config._skipAuthRefresh
      || config._retryAuth
      || url.endsWith('/auth/login')
      || url.endsWith('/auth/logout')
      || url.endsWith('/auth/refresh')
      || url.endsWith('/user/init')
      || url.endsWith('/user/needs_init')
  )
}

async function ensureSessionRefresh() {
  if (!authRefreshInFlight) {
    authRefreshInFlight = import('stores/user')
      .then(({ useUserStore }) => useUserStore().refreshSession())
      .finally(() => {
        authRefreshInFlight = null
      })
  }

  return authRefreshInFlight
}

export default boot(({ app, router }) => {
  api.interceptors.request.use(config => {
    config.headers = config.headers || {}

    const method = String(config.method || 'get').toLowerCase()
    if (!['get', 'head', 'options'].includes(method)) {
      const csrfToken = getCSRFToken(usesRefreshCookie(config) ? 'refresh' : 'access')
      if (csrfToken) {
        config.headers['X-CSRF-TOKEN'] = csrfToken
      }
    }

    return config
  })

  api.interceptors.response.use(
    response => {
      const requestUrl = String(response?.config?.url || '')
      if (requestUrl.endsWith('/auth/login') || requestUrl.endsWith('/auth/refresh') || requestUrl.endsWith('/user/')) {
        authFailureInProgress = false
      }
      return response
    },
    async error => {
      if (error.response && error.response.status === 401) {
        const requestConfig = error.config || {}
        const requestUrl = String(requestConfig.url || '')

        if (!authFailureInProgress && !shouldSkipAuthRefresh(requestConfig) && canRefreshAuthError(error)) {
          try {
            await ensureSessionRefresh()
            return api.request({
              ...requestConfig,
              _retryAuth: true
            })
          } catch {
            // Continue below as a regular auth failure.
          }
        }

        if (!isSessionAuthError(error)) {
          return Promise.reject(error)
        }

        const { useUserStore } = await import('stores/user')
        const userStore = useUserStore()

        markAuthError(error)
        userStore.clearSessionState()
        disconnectAppSocket()

        if (!requestUrl.endsWith('/auth/logout') && !requestUrl.endsWith('/auth/refresh') && router.currentRoute.value.path !== '/login' && !authFailureInProgress) {
          authFailureInProgress = true
          const redirect = router.currentRoute.value.meta?.auth ? router.currentRoute.value.fullPath : null
          const query = redirect ? { redirect } : {}

          try {
            await router.replace({ path: '/login', query })
          } catch {
            // Ignore duplicate or interrupted navigation.
          }
        }
      }
      return Promise.reject(error)
    }
  )

  app.config.globalProperties.$axios = axios
  app.config.globalProperties.$api = api
})

export { api }
