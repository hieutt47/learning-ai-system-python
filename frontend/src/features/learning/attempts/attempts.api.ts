import { apiGet, apiPost } from '../../../shared/api/client'
import type { Id, Page, PageParams } from '../../../shared/api/types'
import type { AttemptDetail, AttemptStart, AttemptStatus, AttemptSummaryItem } from './attempts.types'

interface AttemptSummaryWire {
  id: string | number
  quiz_id: string | number
  quiz_title?: string | null
  course_id?: string | number | null
  course_title?: string | null
  status: AttemptStatus
  score?: number | null
  started_at: string
  submitted_at?: string | null
}

interface AttemptStartWire {
  id: string | number
  quiz_id: string | number
  student_id: string | number
  status: 'IN_PROGRESS'
  total_questions: number
  started_at: string
}

interface AttemptResultWire {
  id: string | number
  quiz_id: string | number
  quiz_title: string
  student_id: string | number
  status: 'SUBMITTED'
  score: number
  total_questions: number
  correct_count: number
  started_at: string
  submitted_at: string
  review: {
    question_id: string | number
    question_content: string
    selected_option_id?: string | number | null
    is_correct: boolean
    correct_option_id: string | number
    explanation?: string | null
    options: { id: string | number; content: string }[]
  }[]
}

const mapResult = (wire: AttemptResultWire): AttemptDetail => ({
  id: String(wire.id),
  quizId: String(wire.quiz_id),
  quizTitle: wire.quiz_title,
  studentId: String(wire.student_id),
  status: wire.status,
  score: Number(wire.score),
  totalQuestions: wire.total_questions,
  correctCount: wire.correct_count,
  startedAt: wire.started_at,
  submittedAt: wire.submitted_at,
  reviews: wire.review.map((review) => ({
    questionId: String(review.question_id),
    questionContent: review.question_content,
    selectedOptionId: review.selected_option_id == null ? null : String(review.selected_option_id),
    isCorrect: review.is_correct,
    correctOptionId: String(review.correct_option_id),
    explanation: review.explanation,
    options: review.options.map((option) => ({ id: String(option.id), content: option.content })),
  })),
})

export const attemptKeys = {
  mine: (params?: PageParams) => ['attempts', 'mine', params] as const,
  detail: (attemptId: string) => ['attempts', 'detail', attemptId] as const,
}

export async function startAttempt(quizId: Id): Promise<AttemptStart> {
  const wire = await apiPost<AttemptStartWire>(`/quizzes/${quizId}/attempts`)
  return {
    id: String(wire.id),
    quizId: String(wire.quiz_id),
    studentId: String(wire.student_id),
    status: wire.status,
    totalQuestions: wire.total_questions,
    startedAt: wire.started_at,
  }
}

export async function submitAttempt(
  attemptId: Id,
  answers: { question_id: Id; selected_option_id: Id | null }[],
): Promise<AttemptDetail> {
  return mapResult(await apiPost<AttemptResultWire>(`/attempts/${attemptId}/submit`, { answers }))
}

export async function getAttemptDetail(attemptId: Id): Promise<AttemptDetail> {
  const wire = await apiGet<AttemptStartWire | AttemptResultWire>(`/attempts/${attemptId}`)
  if (wire.status === 'IN_PROGRESS') {
    throw new Error('Lượt làm bài này chưa được nộp. Hãy quay lại bài quiz để hoàn thành.')
  }
  return mapResult(wire)
}

export async function getMyAttempts(params?: PageParams): Promise<Page<AttemptSummaryItem>> {
  const page = await apiGet<Page<AttemptSummaryWire>>('/users/me/attempts', {
    query: { ...params },
  })
  return {
    ...page,
    items: page.items.map((wire) => ({
      id: String(wire.id),
      quizId: String(wire.quiz_id),
      quizTitle: wire.quiz_title ?? `Quiz #${wire.quiz_id}`,
      courseId: wire.course_id != null ? String(wire.course_id) : undefined,
      courseTitle: wire.course_title ?? undefined,
      status: wire.status,
      score: wire.score != null ? Number(wire.score) : null,
      startedAt: wire.started_at,
      submittedAt: wire.submitted_at ?? null,
    })),
  }
}
