function flattenErrors(value, prefix = '') {
  if (Array.isArray(value)) {
    return value.flatMap(item => flattenErrors(item, prefix))
  }

  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, nestedValue]) => {
      const nextPrefix = prefix ? `${prefix}.${key}` : key
      return flattenErrors(nestedValue, nextPrefix)
    })
  }

  if (value == null || value === '') {
    return []
  }

  return [prefix ? `${prefix}: ${value}` : String(value)]
}

function getApiErrorMessage(error, fallback = 'Error') {
  if (error?.__drasticAuthError) {
    return ''
  }

  const data = error?.response?.data

  if (typeof data?.msg === 'string' && data.msg.trim()) {
    return data.msg
  }

  const errorMessages = flattenErrors(data?.errors)
  if (errorMessages.length > 0) {
    return errorMessages.join(' | ')
  }

  if (typeof data?.message === 'string' && data.message.trim()) {
    return data.message
  }

  return fallback
}

function shouldIgnoreApiError(error) {
  return Boolean(error?.__drasticAuthError || error?.__drasticReauthCancelled)
}

export { getApiErrorMessage, shouldIgnoreApiError }
