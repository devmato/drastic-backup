function markAuthError(error) {
  if (error && typeof error === 'object') {
    error.__drasticAuthError = true
  }
  return error
}

function isAuthError(error) {
  return Boolean(error?.__drasticAuthError || error?.response?.status === 401)
}

function getAuthErrorCode(error) {
  const code = error?.response?.data?.code
  return typeof code === 'string' ? code : ''
}

function canRefreshAuthError(error) {
  return ['missing_token', 'token_expired', 'invalid_token'].includes(getAuthErrorCode(error))
}

function isSessionAuthError(error) {
  return ['missing_token', 'token_expired', 'invalid_token', 'token_revoked', 'fresh_token_required'].includes(getAuthErrorCode(error))
}

function isRecoveryKeyError(error) {
  return Boolean(
    error?.response?.status === 401
      && (
        getAuthErrorCode(error) === 'recovery_key_required'
        || error?.response?.data?.errors?.recovery_key
      )
  )
}

export { canRefreshAuthError, getAuthErrorCode, isAuthError, isRecoveryKeyError, isSessionAuthError, markAuthError }
