import { startAttempt } from './attempts.api'

export const activeAttemptKey = (quizId: string) => ['attempts', 'active', quizId] as const

export function activeAttemptOptions(quizId: string) {
  return {
    queryKey: activeAttemptKey(quizId),
    queryFn: () => startAttempt(quizId),
    enabled: Boolean(quizId),
    retry: false as const,
    staleTime: Infinity,
    refetchOnMount: false as const,
    refetchOnWindowFocus: false as const,
    refetchOnReconnect: false as const,
  }
}
