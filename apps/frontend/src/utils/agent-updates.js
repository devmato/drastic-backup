export function hasVersionMismatch({ version, backend_version }) {
  return Boolean(version && backend_version && version !== 'unknown' && backend_version !== 'unknown'
    && version !== backend_version)
}

export function supportsAgentUpdate(agent) {
  return Boolean(agent && (agent.protocol_version || 0) >= 1 && agent.os?.toLowerCase() === 'linux'
    && (agent.install_type === 'git' || (agent.install_type === 'docker' && agent.protocol_version >= 4)))
}
