const STATUS_LABELS = {
  ACTIVE: '在用',
  INACTIVE: '停用',
  RETIRED: '停用',
  MAINTENANCE: '维护中',
  PLANNED: '待巡检',
  IN_PROGRESS: '处理中',
  SUBMITTED: '待复核',
  VERIFIED: '已复核',
  REPORTED: '已上报',
  CONFIRMED: '已确认',
  REJECTED: '已驳回',
  ASSIGNED: '已分派',
  PENDING_REVIEW: '待复核',
  CLOSED: '已关闭',
  RETURNED: '已退回'
}

const STATUS_TONES = {
  ACTIVE: 'good',
  VERIFIED: 'good',
  CLOSED: 'good',
  INACTIVE: 'warn',
  RETIRED: 'warn',
  MAINTENANCE: 'warn',
  SUBMITTED: 'warn',
  PENDING_REVIEW: 'warn',
  RETURNED: 'warn',
  REPORTED: 'danger',
  REJECTED: 'danger'
}

const SEVERITY_LABELS = {
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高',
  CRITICAL: '紧急'
}

function unwrap(result) {
  if (result && result.ok === true) return Object.prototype.hasOwnProperty.call(result, 'data') ? result.data : result.value
  if (result && result.ok === false) {
    const error = result.error
    throw new Error(error && (error.message || error.code) || '本机数据操作失败')
  }
  if (result && typeof result === 'object' && !Object.prototype.hasOwnProperty.call(result, 'ok')) return result
  throw new Error('本机数据返回格式无效')
}

function failureMessage(error) {
  return error && error.message || '本机数据读取失败，请重试'
}

function statusView(status) {
  const key = String(status || '').toUpperCase()
  return {
    statusText: STATUS_LABELS[key] || status || '未知',
    statusTone: STATUS_TONES[key] || 'neutral'
  }
}

function shortDate(value) {
  if (!value) return '未安排'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return String(value)
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function countText(value) {
  return Number.isFinite(value) ? String(value) : '—'
}

module.exports = {
  SEVERITY_LABELS,
  countText,
  failureMessage,
  shortDate,
  statusView,
  unwrap
}
