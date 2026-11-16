import { getLearningLessons } from '../teaching/lessons/lesson.api'
import { paths } from '../../shared/routing/paths'

export async function learningDestination(courseId: string): Promise<string | null> {
  const lessons = await getLearningLessons(courseId)
  const first = [...lessons].sort((a, b) => a.orderIndex - b.orderIndex)[0]
  return first ? paths.learnLesson(courseId, first.id) : null
}
