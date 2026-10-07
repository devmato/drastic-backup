export function isDatasetWithin(name, parent) {
  return name === parent || name.startsWith(`${parent}/`)
}

export function hasTrueNASSelection(config = {}) {
  return (config.datasets || []).some(name =>
    !(config.exclude_datasets || []).some(parent => isDatasetWithin(name, parent)))
}
