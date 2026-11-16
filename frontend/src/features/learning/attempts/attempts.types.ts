import type { Id, IsoDateTime } from '../../../shared/api/types'

export type AttemptStatus = 'IN_PROGRESS' | 'SUBMITTED'

export interface AttemptSummaryItem {
  id: Id
  quizId: Id
  quizTitle?: string
  courseId?: Id
  courseTitle?: string
  status: AttemptStatus
  score?: number | null
  startedAt: IsoDateTime
  submittedAt?: IsoDateTime | null
}

export interface AttemptStart {
  id: Id
  quizId: Id
  studentId: Id
  status: 'IN_PROGRESS'
  totalQuestions: number
  startedAt: IsoDateTime
}

export interface AttemptAnswerReview {
  questionId: Id
  questionContent: string
  selectedOptionId?: Id | null
  isCorrect: boolean
  correctOptionId: Id
  options: { id: Id; content: string }[]
  explanation?: string | null
}

export interface AttemptDetail {
  id: Id
  quizId: Id
  quizTitle: string
  studentId: Id
  status: 'SUBMITTED'
  score: number
  totalQuestions: number
  correctCount: number
  startedAt: IsoDateTime
  submittedAt: IsoDateTime
  reviews: AttemptAnswerReview[]
}
