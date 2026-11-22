const DEMO = require('./demo-data')

// 运行模式：'auto'（先探测本机，连不上自动降级演示）| 'demo'（强制演示）| 'real'（强制真实）
let mode = 'auto'
let detected = false
const base = 'http://127.0.0.1:8765'
const localHeaders = { 'X-Lab-Local': '1' }

function setMode(next) { mode = next; detected = next !== 'auto' }
function getMode() { return mode }
function isDemo() { return mode === 'demo' || (mode === 'auto' && detected) }
function markDemo() { if (mode === 'auto') detected = true }

// ---- 演示模式：模拟 job 状态机 ----
const demoJobs = {}        // id -> { created, action, resultId, status }
const DEMO_RESULT = {
  train: DEMO.meta.exp_id,
  optimize: DEMO.meta.exp_id,
  industrial_run: DEMO.meta.ind_id,
  industrial_audit: DEMO.meta.ind_id,
  llm_train: DEMO.meta.llm_ids[DEMO.meta.llm_ids.length - 1]
}

function normalize(path) {
  return String(path || '').split('?')[0]
}

function findResponse(path) {
  const key = normalize(path)
  if (Object.prototype.hasOwnProperty.call(DEMO.responses, key)) return DEMO.responses[key]
  return null
}

function demoJobId(action) {
  return 'JOB-DEMO-' + action + '-' + Date.now().toString(36)
}

function simulatedJob(jobId) {
  const job = demoJobs[jobId]
  if (!job) return null
  if (job.cancelled) {
    return {
      id: jobId, action: job.action, status: 'cancelled', created: job.created / 1000,
      error: null, progress: null, result: null,
      events: [{ type: 'demo', message: '演示任务已取消' }]
    }
  }
  const elapsed = Date.now() - job.created
  // 演示任务 2.5 秒后完成，让 watch 能看到 running → completed 的过渡
  const done = elapsed >= 2500
  return {
    id: jobId,
    action: job.action,
    status: done ? 'completed' : 'running',
    created: job.created / 1000,
    error: null,
    progress: done ? { type: 'demo_complete' } : { type: 'demo_running', stage: '演示模式 · 正在回放结果' },
    result: done ? { id: job.resultId, status: 'completed' } : null,
    events: [{ type: 'demo', message: '离线演示模式：回放真实历史结果' }]
  }
}

function demoRequest(path, options = {}) {
  const method = options.method || 'GET'
  const key = normalize(path)
  const data = options.data || {}

  // 提交任务：建一个模拟 job
  if (key === '/api/jobs' && method === 'POST') {
    const action = data.action || 'train'
    const id = demoJobId(action)
    demoJobs[id] = { created: Date.now(), action, resultId: DEMO_RESULT[action] || DEMO.meta.exp_id }
    // 同时插入列表，让 /api/jobs 能看到
    return Promise.resolve({ id, status: 'running', action })
  }
  // 取消任务
  if (/\/api\/jobs\/.*\/cancel/.test(key)) {
    const id = key.split('/')[3]
    if (demoJobs[id]) demoJobs[id].cancelled = true
    return Promise.resolve({ id, status: 'cancelled' })
  }
  // 单个 job（含模拟 job）
  const jobMatch = key.match(/^\/api\/jobs\/([^/]+)$/)
  if (jobMatch && method === 'GET') {
    const sim = simulatedJob(jobMatch[1])
    if (sim) return Promise.resolve(sim)
  }
  // LLM 状态
  if ((key === '/api/llm/status' || (key === '/api/jobs' && data.action === 'llm_status'))) {
    return Promise.resolve({ sdk: true, key: true, message: '演示模式：内置就绪状态' })
  }

  const resp = findResponse(path)
  if (resp !== null) {
    // 对 /api/jobs 列表，把模拟 job 合并进去
    if (key === '/api/jobs' && method === 'GET') {
      const sims = Object.keys(demoJobs).map(simulatedJob).filter(Boolean)
      return Promise.resolve(sims.concat(Array.isArray(resp) ? resp : []))
    }
    return Promise.resolve(resp)
  }
  // 兜底：尽量给出结构合理的空响应，避免前端报错
  if (key.indexOf('/trials') >= 0) return Promise.resolve({ total: 0, page: 1, size: 20, rows: [] })
  if (key.indexOf('/events') >= 0) return Promise.resolve({ total: 0, page: 1, size: 50, rows: [] })
  if (key.indexOf('/charts') >= 0) return Promise.resolve({ history: [], distribution: {}, scatter: [], status: [], parameters: [], objective_label: '目标值' })
  return Promise.resolve({})
}

function request(path, options = {}) {
  if (isDemo()) {
    markDemo()
    return demoRequest(path, options)
  }
  return new Promise((resolve, reject) => wx.request({
    url: base + path,
    method: options.method || 'GET',
    data: options.data,
    responseType: options.responseType || 'text',
    header: { 'content-type': options.contentType || 'application/json', ...localHeaders },
    success(res) {
      if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data)
      else reject(new Error((res.data && res.data.error) || '请求失败 ' + res.statusCode))
    },
    fail(error) { reject(new Error(error.errMsg || '无法连接后端')) }
  }))
}

// 探测本机后端是否可用；不可用则切换到演示模式
function probe() {
  return new Promise(resolve => {
    wx.request({
      url: base + '/api/me',
      method: 'GET',
      header: localHeaders,
      timeout: 2500,
      success(res) { resolve(res.statusCode >= 200 && res.statusCode < 300) },
      fail() { resolve(false) }
    })
  }).then(ok => {
    if (!ok) markDemo()
    return ok
  })
}

module.exports = { request, base, localHeaders, setMode, getMode, isDemo, markDemo, probe }
