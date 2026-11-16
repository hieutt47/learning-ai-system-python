import type { IsoDateTime } from '../api/types'

const VIETNAM_TIMEZONE = 'Asia/Ho_Chi_Minh'
const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const ZONED_DATETIME_PATTERN = /(?:Z|[+-]\d{2}:?\d{2})$/i

const vnDateTimeFormatter = new Intl.DateTimeFormat('vi-VN', {
  timeZone: VIETNAM_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const vnDateFormatter = new Intl.DateTimeFormat('vi-VN', {
  timeZone: VIETNAM_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function partsMap(parts: Intl.DateTimeFormatPart[]): Record<string, string> {
  const map: Record<string, string> = {}
  for (const part of parts) {
    if (part.type !== 'literal') {
      map[part.type] = part.value
    }
  }
  return map
}

function parseZonedDate(value?: IsoDateTime | null): Date | null {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed || !ZONED_DATETIME_PATTERN.test(trimmed)) {
    return null
  }
  const timestamp = Date.parse(trimmed)
  if (!Number.isFinite(timestamp)) return null
  return new Date(timestamp)
}

function parseDateOnly(value?: string | null): { day: string; month: string; year: string } | null {
  if (!value) return null
  const match = DATE_ONLY_PATTERN.exec(value.trim())
  if (!match) return null
  const [, year, month, day] = match
  const y = Number(year)
  const m = Number(month)
  const d = Number(day)
  const probe = new Date(Date.UTC(y, m - 1, d))
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) {
    return null
  }
  return { day, month, year }
}

/** Hiển thị timestamp có Z/offset theo giờ Việt Nam (Asia/Ho_Chi_Minh): DD/MM/YYYY HH:mm. */
export function formatDateTime(value?: IsoDateTime | null): string {
  const dateOnly = parseDateOnly(value)
  if (dateOnly) {
    return `${dateOnly.day}/${dateOnly.month}/${dateOnly.year}`
  }
  const parsed = parseZonedDate(value)
  if (!parsed) return '—'
  const p = partsMap(vnDateTimeFormatter.formatToParts(parsed))
  const hour = p.hour === '24' ? '00' : p.hour
  return `${p.day}/${p.month}/${p.year} ${hour}:${p.minute}`
}

/** Hiển thị ngày theo giờ Việt Nam (Asia/Ho_Chi_Minh) hoặc date-only không lệch ngày: DD/MM/YYYY. */
export function formatDate(value?: IsoDateTime | null): string {
  const dateOnly = parseDateOnly(value)
  if (dateOnly) {
    return `${dateOnly.day}/${dateOnly.month}/${dateOnly.year}`
  }
  const parsed = parseZonedDate(value)
  if (!parsed) return '—'
  const p = partsMap(vnDateFormatter.formatToParts(parsed))
  return `${p.day}/${p.month}/${p.year}`
}
