export function hasVersionMismatch({ version, backend_version }) {
  return Boolean(version && backend_version && version !== 'unknown' && backend_version !== 'unknown'
    && version !== backend_version)
}

export function supportsAgentUpdate(agent) {
  const os = agent?.os?.toLowerCase()
  return Boolean(agent && (agent.protocol_version || 0) >= 1
    && ((agent.install_type === 'git' && ['linux', 'darwin'].includes(os))
      || (agent.install_type === 'docker' && os === 'linux' && agent.protocol_version >= 4)))
}
