export function supportsJob(agent, type) {
  if (type === 'file') return true
  if (!agent) return false
  if ((agent.protocol_version || 0) < 3) return type === 'proxmox'
  const connection = agent.connections?.[type]
  return !!(connection?.configured && connection?.available)
}
