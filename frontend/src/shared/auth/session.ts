import {
  advanceSessionGeneration,
  apiGet,
  apiPost,
  getSessionGeneration,
  isApiError,
  requireSessionGeneration,
  setAccessTokenProvider,
  setRefreshTokenHandler,
  setSessionExpiredHandler,
} from '../api/client'
import { queryClient } from '../api/queryClient'
import type { AuthResponse, ChangePasswordRequest, LoginRequest, ResetPasswordRequest, User } from './types'

interface SessionState {
  user: User | null
  isLoading: boolean
  error: unknown
}

let state: SessionState = { user: null, isLoading: true, error: null }
let accessToken: string | null = null
let refreshPromise: Promise<void> | null = null
let restorePromise: Promise<void> | null = null
let logoutPromise: Promise<void> | null = null
let logoutGeneration: number | null = null
// Cookie-changing operations must finish in order, even if their UI result is stale.
let cookieQueue: Promise<unknown> = Promise.resolve()
const listeners = new Set<() => void>()

function queueCookieOperation<T>(operation: () => Promise<T>): Promise<T> {
  const result = cookieQueue.then(operation, operation)
  cookieQueue = result.then(() => undefined, () => undefined)
  return result
}

function updateSession(nextState: SessionState): void {
  state = nextState
  listeners.forEach((listener) => listener())
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getSession(): SessionState {
  return state
}

export function updateCurrentUser(user: User): void {
  if (state.user?.id === user.id) {
    updateSession({ ...state, user })
  }
}

function clearSession(): number {
  const generation = advanceSessionGeneration()
  accessToken = null
  refreshPromise = null
  restorePromise = null
  queryClient.clear()
  updateSession({ user: null, isLoading: false, error: null })
  return generation
}

function refreshAccessToken(): Promise<void> {
  if (logoutPromise) {
    return Promise.reject(new DOMException('Đang đăng xuất', 'AbortError'))
  }
  if (!refreshPromise) {
    const generation = getSessionGeneration()
    const pending = queueCookieOperation(async () => {
      requireSessionGeneration(generation)
      const response = await apiPost<AuthResponse>('/auth/refresh', undefined, { auth: false })
      requireSessionGeneration(generation)
      accessToken = response.access_token
    }).finally(() => {
      if (refreshPromise === pending) refreshPromise = null
    })
    refreshPromise = pending
  }
  return refreshPromise
}

export function restoreSession(): Promise<void> {
  if (logoutPromise) return logoutPromise
  if (!restorePromise) {
    const generation = getSessionGeneration()
    updateSession({ ...state, isLoading: true, error: null })
    const pending = (async () => {
      try {
        await refreshAccessToken()
        requireSessionGeneration(generation)
        const user = await apiGet<User>('/users/me')
        requireSessionGeneration(generation)
        updateSession({ user, isLoading: false, error: null })
      } catch (error) {
        if (generation !== getSessionGeneration()) return
        clearSession()
        if (!(isApiError(error) && (error.status === 401 || error.code === 'ACCOUNT_INACTIVE' || error.code === 'EMAIL_NOT_VERIFIED'))) {
          updateSession({ user: null, isLoading: false, error })
        }
      }
    })().finally(() => {
      if (restorePromise === pending) restorePromise = null
    })
    restorePromise = pending
  }
  return restorePromise
}

export async function login(request: LoginRequest): Promise<User> {
  const generation = clearSession()
  try {
    const response = await queueCookieOperation(async () => {
      requireSessionGeneration(generation)
      return apiPost<AuthResponse>('/auth/login', request, { auth: false })
    })
    requireSessionGeneration(generation)
    accessToken = response.access_token
    const user = await apiGet<User>('/users/me')
    requireSessionGeneration(generation)
    updateSession({ user, isLoading: false, error: null })
    return user
  } catch (error) {
    if (generation === getSessionGeneration()) clearSession()
    throw error
  }
}

export function logout(): Promise<void> {
  if (!logoutPromise || logoutGeneration !== getSessionGeneration()) {
    logoutGeneration = clearSession()
    const pending = queueCookieOperation(() => apiPost<void>('/auth/logout', undefined, { auth: false }))
      .finally(() => {
        if (logoutPromise === pending) {
          logoutPromise = null
          logoutGeneration = null
        }
      })
    logoutPromise = pending
  }
  return logoutPromise
}

export function changePassword(request: ChangePasswordRequest): Promise<void> {
  return apiPost<void>('/auth/change-password', request)
}

export async function resetPassword(request: ResetPasswordRequest): Promise<void> {
  const generation = getSessionGeneration()
  await queueCookieOperation(async () => {
    requireSessionGeneration(generation)
    await apiPost<void>('/auth/reset-password', request, { auth: false })
    requireSessionGeneration(generation)
    clearSession()
  })
}

export async function logoutAll(): Promise<void> {
  const generation = getSessionGeneration()
  await queueCookieOperation(async () => {
    requireSessionGeneration(generation)
    // Do not refresh inside this queue: refresh uses the same queue and would deadlock.
    await apiPost<void>('/auth/logout-all', undefined, { retryAuth: false })
    requireSessionGeneration(generation)
    clearSession()
  })
}

setAccessTokenProvider(() => accessToken)
setRefreshTokenHandler(refreshAccessToken)
setSessionExpiredHandler(clearSession)
