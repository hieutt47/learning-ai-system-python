import { Navigate, useLocation } from 'react-router'
import { paths } from './paths'

export function NewLessonRedirect() {
  const { search } = useLocation()
  return <Navigate to={`${paths.teachingLesson('new')}${search}`} replace />
}
