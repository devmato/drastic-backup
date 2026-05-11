function getCookieValue(name) {
  if (typeof document === 'undefined') {
    return null
  }

  const cookie = document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${name}=`))

  if (!cookie) {
    return null
  }

  const value = cookie.split('=').slice(1).join('=')
  return value ? decodeURIComponent(value) : null
}

function getCSRFToken(kind = 'access') {
  return getCookieValue(kind === 'refresh' ? 'csrf_refresh_token' : 'csrf_access_token')
}

export { getCookieValue, getCSRFToken }
