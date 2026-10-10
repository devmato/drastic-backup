// Shared literal-path inheritance for file, dataset and host/VM browsers.
export function isPathWithin(entry, parent) {
  if (parent.group === 'pattern') return false
  const path = entry.path.replace(/\/+$/, '') || '/'
  const base = parent.path.replace(/\/+$/, '') || '/'
  return path === base || (parent.group !== 'file' && parent.group !== 'vm'
    && (base === '/' ? path.startsWith('/') : path.startsWith(`${base}/`)))
}

export function pathSelectionOptions(entry, selection, contains = isPathWithin) {
  let rule = null
  for (const [state, entries] of [['include', selection.paths || []], ['exclude', selection.exclude_patterns || []]]) {
    for (const parent of entries) {
      if (!contains(entry, parent)) continue
      if (!rule || (contains(parent, rule.entry) && !contains(rule.entry, parent))
        || (parent.path === rule.entry.path && state === 'exclude')) {
        rule = { state, entry: parent }
      }
    }
  }
  const explicit = !!rule && rule.entry.path === entry.path
  const action = rule?.state === 'exclude' ? 'Excluded' : 'Included'
  return {
    state: rule?.state || null,
    explicit,
    note: !rule ? '' : explicit ? `Explicitly ${action.toLowerCase()}` : `${action} via ${rule.entry.label || rule.entry.path}`,
    includeDisabled: entry.readable === false || (explicit && rule.state === 'include'),
    excludeDisabled: entry.readable === false || (explicit && rule.state === 'exclude'),
  }
}
