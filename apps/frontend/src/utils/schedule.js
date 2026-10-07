export function scheduleTiming(schedule = {}) {
  if (schedule.timing) return JSON.parse(JSON.stringify(schedule.timing))
  const parts = schedule.cron_string?.split(' ') || []
  const days = schedule.day_of_week || (!parts[4] || parts[4] === '*' ? [0, 1, 2, 3, 4, 5, 6] : parts[4].split(',').map(Number))
  if (parts.length && (!/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1]) || parts[2] !== '*' || parts[3] !== '*' || days.some(day => !Number.isInteger(day)))) {
    return { type: 'cron', expression: schedule.cron_string }
  }
  const timing = { type: days.length === 7 ? 'daily' : 'weekly', hour: Number(schedule.hour ?? parts[1] ?? 0), minute: Number(schedule.minute ?? parts[0] ?? 0) }
  return timing.type === 'weekly' ? { ...timing, weekdays: [...days] } : timing
}

export function describeTiming(timing) {
  const time = `${String(timing.hour ?? 0).padStart(2, '0')}:${String(timing.minute ?? 0).padStart(2, '0')}`
  const days = (timing.weekdays || []).map(day => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][day]).join(', ')
  switch (timing.type) {
    case 'hourly': return `Hourly at minute ${timing.minute}${days ? ` · ${days}` : ''}${timing.from_hour !== undefined ? ` · Hours ${timing.from_hour}–${timing.to_hour}` : ''}`
    case 'daily': return `Daily at ${time}`
    case 'weekly': return `${days} at ${time}`
    case 'monthly': return `Day ${timing.day} of each month at ${time}`
    case 'yearly': return `Months ${(timing.months || []).join(', ')} · Day ${timing.day} at ${time}`
    case 'once': return `Once on ${timing.date} at ${time}`
    case 'periodic': return `Every ${timing.interval} minutes, offset ${timing.offset} minutes`
    default: return timing.expression
  }
}

export function sameSchedule(left, right) {
  if (!left || !right) return false
  const settings = schedule => {
    const timing = Object.entries(scheduleTiming(schedule)).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, value]) => [key, Array.isArray(value) ? [...value].sort((a, b) => a - b) : value])
    const check = schedule.config?.repository_check
    return [timing, Boolean(schedule.enabled), schedule.repository_id, schedule.retention_id || null,
      Boolean(check?.enabled), check?.enabled ? String(check.read_data || '').trim() || null : null]
  }
  return JSON.stringify(settings(left)) === JSON.stringify(settings(right))
}
