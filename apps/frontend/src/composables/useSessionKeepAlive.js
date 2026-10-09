import { onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ensureSocket } from 'src/utils/socket'

// Keep open authenticated views alive, including views driven only by Socket.IO.
export function useSessionKeepAlive(userStore) {
  const router = useRouter()
  let timer = null
  let disposed = false
  let inFlight = false

  async function keepAlive() {
    if (disposed || !userStore.loggedIn || inFlight) return
    inFlight = true
    try {
      await userStore.fetchAuthStatus()
      if (disposed) return
      if (userStore.loggedIn) {
        await ensureSocket()
      } else if (router.currentRoute.value.meta?.auth) {
        await router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
      }
    } catch {
      // HTTP authentication failures are handled centrally; retry transient failures later.
    } finally {
      inFlight = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void keepAlive()
  }

  onMounted(() => {
    timer = setInterval(keepAlive, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })
  onBeforeUnmount(() => {
    disposed = true
    clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })
}
