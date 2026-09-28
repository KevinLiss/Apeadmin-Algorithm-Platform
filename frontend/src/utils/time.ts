/**
 * 时间显示工具。
 *
 * 后端 created_at / started_at 等字段存的是 naive UTC 字符串
 * （形如 "2026-09-24T08:53:12"，无 Z 无偏移）。直接渲染会慢 8 小时，
 * 必须补 Z 按 UTC 解析后再转浏览器本地时区显示。
 * 与监控台 formatTime 保持同一口径。
 */

function parseUtc(s: string): Date | null {
  const iso = s.endsWith('Z') || s.includes('+') ? s : s + 'Z'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? null : d
}

const pad = (n: number) => String(n).padStart(2, '0')

/** naive UTC 字符串 → 本地 "YYYY-MM-DD HH:mm:ss"；无法解析时原样返回，空值返回 '—' */
export function formatDateTime(s?: string | null): string {
  if (!s) return '—'
  const d = parseUtc(s)
  if (!d) return s
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** naive UTC 字符串 → 本地 "HH:mm:ss"（时间线等只需时刻的场景） */
export function formatClock(s?: string | null): string {
  if (!s) return ''
  const d = parseUtc(s)
  if (!d) return s
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
