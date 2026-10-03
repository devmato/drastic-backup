export const connectionTypes = [
  { value: 'proxmox', label: 'Proxmox', minProtocol: 1 },
  { value: 'truenas', label: 'TrueNAS', minProtocol: 3 },
]

export function getAgentConnections(agent) {
  const protocol = agent?.protocol_version || 0
  return connectionTypes
    .filter(type => protocol >= type.minProtocol)
    .map(type => ({ ...type, configured: null, available: null, ...agent.connections?.[type.value] }))
    .filter(connection => connection.configured || protocol < 3)
}

export function getAvailableConnectionTypes(agent) {
  if ((agent?.protocol_version || 0) < 3) return []
  return connectionTypes.filter(type => !agent.connections?.[type.value]?.configured)
}

export function supportsJob(agent, type) {
  if (type === 'file') return true
  if (!agent) return false
  if ((agent.protocol_version || 0) < 3) return type === 'proxmox'
  const connection = agent.connections?.[type]
  return !!(connection?.configured && connection?.available)
}
