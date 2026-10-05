import { io } from 'socket.io-client'
import { recordBrowserDiagnostic } from 'src/utils/diagnostics'

let socketPromise = null
let socketInstance = null

export async function ensureSocket() {
  if (typeof window === 'undefined') {
    return null
  }

  if (socketInstance) {
    if (!socketInstance.connected) {
      socketInstance.connect()
    }
    return socketInstance
  }

  if (!socketPromise) {
    socketPromise = Promise.resolve()
      .then(() => {
        socketInstance = io('/', {
          path: '/socket.io',
          autoConnect: false,
          withCredentials: true
        })
        socketInstance.on('connect_error', (error) => {
          recordBrowserDiagnostic('socket.error')
          console.warn('Socket.IO connection failed:', error.message)
        })
        socketInstance.on('disconnect', (reason) => {
          recordBrowserDiagnostic('socket.disconnected')
          if (reason === 'io server disconnect') {
            console.warn('Socket.IO disconnected by server')
          }
        })
        socketInstance.on('connect', () => recordBrowserDiagnostic('socket.connected'))
        socketInstance.on('event', payload => {
          const operationId = payload?.name?.match(/^operationupdate(\d+)$/)?.[1]
          if (operationId) recordBrowserDiagnostic('operation.notice', Number(operationId))
        })
        socketInstance.connect()
        return socketInstance
      })
      .catch((error) => {
        socketPromise = null
        throw error
      })
  }

  return socketPromise
}

export async function subscribeToSocketEvents(handler) {
  let socket = null

  try {
    socket = await ensureSocket()
  } catch {
    return () => {}
  }

  if (!socket) {
    return () => {}
  }

  socket.on('event', handler)

  return () => {
    socket.off('event', handler)
  }
}

export function disconnectAppSocket() {
  if (socketInstance) {
    socketInstance.disconnect()
  }
  socketInstance = null
  socketPromise = null
}
