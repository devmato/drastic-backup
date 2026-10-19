// Retention descriptions and editor values share the API's rule representation.
export const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

export const ruleTypes = [
  { value: 'last', label: 'Last N backups' },
  ...['hour', 'day', 'week', 'month', 'year'].map(period => ({
    value: `last:${period}`, label: `Last N backups, at most one per ${period}`,
  })),
  { value: 'recent', label: 'All backups in the last…' },
  { value: 'since', label: 'All backups after a specific date' },
  ...['day', 'week', 'month', 'year'].map(period => ({
    value: `calendar:${period}`, label: `One backup each ${period}, for the last N ${period}s`,
  })),
]

export function describeRetentionRule(rule) {
  if (rule.type === 'last') {
    return `Last ${rule.count} backups${rule.period ? `, at most one per ${rule.period}` : ''}`
  }
  if (rule.type === 'recent') {
    const span = ['days', 'weeks', 'months'].filter(key => rule[key] > 0).map(key => `${rule[key]} ${rule[key] === 1 ? key.slice(0, -1) : key}`).join(' and ')
    return `All backups in the last ${span}`
  }
  if (rule.type === 'since') return `All backups after ${rule.date}`
  const selection = rule.period === 'week' ? ` on ${weekdays[rule.weekday]}`
    : rule.period === 'month' ? ` on day ${rule.day}`
      : rule.period === 'year' ? ` in ${months[rule.month - 1]}` : ''
  return `One backup each ${rule.period}${selection}, for the last ${rule.count} ${rule.period}${rule.count === 1 ? '' : 's'}`
}

export function buildRetentionRule(selection, form) {
  const [type, period] = selection.split(':')
  if (type === 'recent') return { type, days: form.days, weeks: form.weeks, months: form.months }
  if (type === 'since') return { type, date: form.date }
  const rule = { type, count: form.count, ...(period ? { period } : {}) }
  if (type === 'calendar') {
    const selector = { week: 'weekday', month: 'day', year: 'month' }[period]
    if (selector) rule[selector] = form[selector]
  }
  return rule
}
