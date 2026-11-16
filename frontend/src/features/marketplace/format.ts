export { formatDate, formatDateTime } from '../../shared/utils/format'

export function formatPrice(price: string, currency = 'VND'): string {
  const value = Number(price)
  if (!Number.isFinite(value) || value === 0) {
    return 'Miễn phí'
  }
  return new Intl.NumberFormat('vi-VN', { style: 'currency', currency }).format(value)
}
