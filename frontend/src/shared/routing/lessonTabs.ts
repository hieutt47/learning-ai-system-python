import type { Params } from 'react-router'
import { paths } from './paths'
import type { TabItem } from './routeHandle'

export function lessonTabs(params: Params): TabItem[] {
  const lessonId = params.lessonId ?? ''
  // A new lesson has no persisted ID. Never link its tabs to APIs with "new".
  if (!/^[1-9][0-9]{0,18}$/.test(lessonId) || BigInt(lessonId) > 9223372036854775807n) return []
  return [
    { to: paths.teachingLesson(lessonId), label: 'Nội dung' },
    { to: paths.teachingLessonQuizzes(lessonId), label: 'Quiz' },
    { to: paths.teachingLessonAi(lessonId), label: 'AI & Flashcard' },
  ]
}
