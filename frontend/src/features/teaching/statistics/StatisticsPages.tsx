import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Descriptions, Select, Space, Table, Typography } from 'antd'
import { useParams } from 'react-router'
import { apiGet } from '../../../shared/api/client'
import { ErrorState, LoadingState } from '../../../shared/components/PageState'
import { usePageHeader } from '../../../shared/components/pageHeader'
import { formatDateTime } from '../../../shared/utils/format'
import { listOwnedCourses } from '../../marketplace/course.api'

interface CourseStats {
  enrolled_students: number; quiz_count: number; submitted_attempts: number
  completed_students: number; completion_rate: number; generated_at: string
}
interface QuizStats {
  submitted_attempts: number; unique_students: number; average_score: number | null; generated_at: string
  best_scores: { student_id: string; score: number }[]
  hardest_questions: { question_id: string; wrong_count: number }[]
}
interface SystemStats {
  active_users: number; teachers: number; published_courses: number
  enrollments: number; submitted_attempts: number; generated_at: string
}

export function CourseStatisticsPage() {
  usePageHeader({ title: 'Thống kê khóa học' })
  const [courseId, setCourseId] = useState<string>()
  const courses = useQuery({ queryKey: ['statistics', 'courses', 'options'],
    queryFn: () => listOwnedCourses({ page: 1, page_size: 100 }) })
  const selected = courseId ?? courses.data?.items[0]?.id
  const stats = useQuery({ queryKey: ['statistics', 'course', selected], enabled: !!selected,
    queryFn: () => apiGet<CourseStats>(`/statistics/courses/${selected}`) })
  if (courses.isLoading) return <LoadingState />
  if (courses.isError) return <ErrorState error={courses.error} onRetry={() => void courses.refetch()} />
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Select aria-label="Chọn khóa học" placeholder="Chọn khóa học" value={selected} onChange={setCourseId}
      style={{ width: '100%', maxWidth: 480 }} options={courses.data?.items.map(c => ({ label: c.title, value: c.id }))} />
    {!selected ? <Typography.Text>Chưa có khóa học để thống kê.</Typography.Text>
      : stats.isLoading ? <LoadingState />
      : stats.isError ? <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
      : stats.data ? <Card title="Kết quả học tập"><Descriptions bordered column={1} items={[
        { key: 'members', label: 'Sinh viên ghi danh', children: stats.data.enrolled_students },
        { key: 'quizzes', label: 'Quiz đang xuất bản', children: stats.data.quiz_count },
        { key: 'submitted', label: 'Lượt đã nộp', children: stats.data.submitted_attempts },
        { key: 'completed', label: 'Sinh viên hoàn thành', children: stats.data.completed_students },
        { key: 'rate', label: 'Tỷ lệ hoàn thành', children: `${stats.data.completion_rate}%` },
        { key: 'time', label: 'Thời điểm thống kê (giờ Việt Nam)', children: formatDateTime(stats.data.generated_at) },
      ]} /><Typography.Paragraph type="secondary">Hoàn thành = đã nộp mọi quiz hiện đang xuất bản. Không có sinh viên hoặc quiz xuất bản: tỷ lệ 0%.</Typography.Paragraph></Card> : null}
  </Space>
}

export function QuizStatisticsPage() {
  const { quizId = '' } = useParams()
  usePageHeader({ title: 'Thống kê quiz' })
  const stats = useQuery({ queryKey: ['statistics', 'quiz', quizId],
    queryFn: () => apiGet<QuizStats>(`/statistics/quizzes/${quizId}`), enabled: !!quizId })
  if (stats.isLoading) return <LoadingState />
  if (stats.isError) return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
  if (!stats.data) return null
  const data = stats.data
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Card title="Tổng hợp lượt đã nộp"><Descriptions bordered column={1} items={[
      { key: 'submitted', label: 'Lượt đã nộp', children: data.submitted_attempts },
      { key: 'students', label: 'Sinh viên đã nộp', children: data.unique_students },
      { key: 'average', label: 'Điểm trung bình mọi lượt nộp', children: data.average_score ?? 'Chưa có lượt nộp' },
      { key: 'time', label: 'Thời điểm thống kê (giờ Việt Nam)', children: formatDateTime(data.generated_at) },
    ]} /></Card>
    <Card title="Điểm tốt nhất theo sinh viên"><Table rowKey="student_id" dataSource={data.best_scores}
      columns={[{ title: 'Mã sinh viên', dataIndex: 'student_id' }, { title: 'Điểm / 100', dataIndex: 'score' }]} /></Card>
    <Card title="Câu sai nhiều nhất (gồm bỏ trống)"><Table rowKey="question_id" dataSource={data.hardest_questions}
      columns={[{ title: 'Mã câu hỏi', dataIndex: 'question_id' }, { title: 'Số lượt sai', dataIndex: 'wrong_count' }]} /></Card>
  </Space>
}

export function SystemStatisticsPage() {
  usePageHeader({ title: 'Thống kê hệ thống' })
  const stats = useQuery({ queryKey: ['statistics', 'system'], queryFn: () => apiGet<SystemStats>('/statistics/system') })
  if (stats.isLoading) return <LoadingState />
  if (stats.isError) return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
  if (!stats.data) return null
  const d = stats.data
  return <Card title="Tổng hợp hệ thống"><Descriptions bordered column={1} items={[
    { key: 'users', label: 'Tài khoản hoạt động', children: d.active_users },
    { key: 'teachers', label: 'Giảng viên', children: d.teachers },
    { key: 'courses', label: 'Khóa học đang xuất bản', children: d.published_courses },
    { key: 'enrollments', label: 'Lượt ghi danh', children: d.enrollments },
    { key: 'attempts', label: 'Lượt đã nộp', children: d.submitted_attempts },
    { key: 'time', label: 'Thời điểm thống kê (giờ Việt Nam)', children: formatDateTime(d.generated_at) },
  ]} /></Card>
}
