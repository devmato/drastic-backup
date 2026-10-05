import { getCSRFToken } from 'src/utils/security'

let enabled = false
const lastSent = new Map()

export function setDiagnosticRecording(value) {
  enabled = Boolean(value)
}

export function recordBrowserDiagnostic(eventType, operationId = null, status = null) {
  if (!enabled || Date.now() - (lastSent.get(eventType) || 0) < 10000) return
  lastSent.set(eventType, Date.now())
  // Diagnostics must not trigger authentication redirects or delay the UI.
  void fetch('/api/user/debug/browser', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': getCSRFToken('access') || '' },
    body: JSON.stringify({ event_type: eventType, operation_id: operationId, status,
      occurred_at: new Date().toISOString(), frontend_version: process.env.APP_VERSION,
      frontend_build: process.env.BUILD_TIME }),
  }).catch(() => {})
}
