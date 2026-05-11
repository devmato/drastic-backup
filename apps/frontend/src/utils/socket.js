import { io } from 'socket.io-client'

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
          console.warn('Socket.IO connection failed:', error.message)
        })
        socketInstance.on('disconnect', (reason) => {
          if (reason === 'io server disconnect') {
            console.warn('Socket.IO disconnected by server')
          }
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
