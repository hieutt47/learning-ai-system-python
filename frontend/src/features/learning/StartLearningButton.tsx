import { useMutation } from '@tanstack/react-query'
import { App, Button } from 'antd'
import { useNavigate } from 'react-router'
import { learningDestination } from './startLearning'

export function StartLearningButton({ courseId, block = false, size }: {
  courseId: string; block?: boolean; size?: 'small' | 'middle' | 'large'
}) {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const start = useMutation({
    mutationFn: () => learningDestination(courseId),
    onSuccess: (destination) => {
      if (!destination) {
        void message.info('Khóa học chưa có bài học. Vui lòng quay lại sau.')
        return
      }
      void navigate(destination)
    },
    onError: (error: Error) => void message.error(error.message),
  })
  return <Button type="primary" block={block} size={size} loading={start.isPending}
    disabled={start.isPending} onClick={() => start.mutate()}>Vào học</Button>
}
