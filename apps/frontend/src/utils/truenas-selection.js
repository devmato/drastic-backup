export function isDatasetWithin(name, parent) {
  return name === parent || name.startsWith(`${parent}/`)
}

export function hasTrueNASSelection(config = {}) {
  return (config.paths || []).some(entry =>
    !(config.exclude_paths || []).some(parent => isPathWithin(entry, parent)))
}

export function isPathWithin(entry, parent, datasets = []) {
  if (entry.dataset === parent.dataset) {
    return parent.path === '.' || entry.path === parent.path
      || (parent.group !== 'file' && isDatasetWithin(entry.path, parent.path))
  }
  if (!isDatasetWithin(entry.dataset, parent.dataset)) return false
  if (parent.path === '.') return true
  const source = datasets.find(dataset => dataset.id === parent.dataset)?.mountpoint
  const target = datasets.find(dataset => dataset.id === entry.dataset)?.mountpoint
  return parent.group !== 'file' && !!source && !!target
    && isDatasetWithin(`${target}${entry.path === '.' ? '' : `/${entry.path}`}`, `${source}/${parent.path}`)
}

export function browserPath(entry) {
  return `/${entry.dataset}${entry.path === '.' ? '' : `/${entry.path}`}`
}

export function browserKey(entry) {
  return JSON.stringify([entry.dataset, entry.path])
}

export function browserSelection(config) {
  const entries = values => (values || []).map(entry => ({
    ...entry, relativePath: entry.path, path: browserKey(entry), label: browserPath(entry),
  }))
  return { paths: entries(config.paths), exclude_patterns: entries(config.exclude_paths) }
}

export function selectionConfig(selection) {
  const entries = values => values.map(entry => ({ dataset: entry.dataset, path: entry.relativePath, group: entry.group }))
  return { paths: entries(selection.paths), exclude_paths: entries(selection.exclude_patterns) }
}
