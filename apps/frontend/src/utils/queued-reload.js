// ponytail: only background reloads; one request and one pending follow-up per queue.
export function createQueuedReload(loader, interval = 1000) {
  let inFlight = false
  let queued = false
  let urgent = false
  let timer = null
  let lastStarted = -Infinity
  let cancelled = false

  function schedule() {
    if (cancelled || inFlight || timer !== null || !queued) return
    const delay = urgent ? 0 : Math.max(0, lastStarted + interval - Date.now())
    if (delay > 0) {
      timer = setTimeout(() => {
        timer = null
        void run()
      }, delay)
      return
    }
    return run()
  }

  async function run() {
    inFlight = true
    queued = false
    urgent = false
    lastStarted = Date.now()
    try {
      await loader()
    } catch (error) {
      console.warn('Background reload failed:', error.message)
    } finally {
      inFlight = false
      schedule()
    }
  }

  function reload(immediate = false) {
    if (cancelled) return
    queued = true
    urgent ||= immediate
    if (urgent && timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    return schedule()
  }

  reload.cancel = () => {
    cancelled = true
    clearTimeout(timer)
  }

  return reload
}
