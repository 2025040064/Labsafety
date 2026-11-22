// Lightweight canvas 2d charts; the backend already computed every series.
const colors = {
  text: '#374151', grid: '#e5e7eb', axis: '#9ca3af', primary: '#2563eb',
  accent: '#dc2626', muted: '#cbd5e1', fill: 'rgba(37, 99, 235, 0.14)'
}

const getContext = (page, id) => new Promise((resolve, reject) => {
  wx.createSelectorQuery().in(page).select('#' + id)
    .fields({ node: true, size: true }, result => {
      if (!result || !result.node) return reject(new Error('图表画布不可用'))
      const canvas = result.node
      const info = (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()) || {}
      const dpr = info.pixelRatio || 1
      canvas.width = Math.max(1, Math.round(result.width * dpr))
      canvas.height = Math.max(1, Math.round(result.height * dpr))
      const ctx = canvas.getContext('2d')
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      resolve({ ctx, width: result.width, height: result.height })
    }).exec()
})

const bounds = values => {
  const list = values.filter(value => typeof value === 'number' && isFinite(value))
  if (!list.length) return null
  let low = Math.min(...list), high = Math.max(...list)
  if (low === high) { low -= 1; high += 1 }
  return { low, high }
}

const frame = (ctx, width, height, title, xLabel, yLabel) => {
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  const pad = { left: 44, right: 12, top: 26, bottom: 26 }
  const plot = { x: pad.left, y: pad.top, w: width - pad.left - pad.right, h: height - pad.top - pad.bottom }
  ctx.strokeStyle = colors.grid
  ctx.lineWidth = 1
  for (let index = 0; index <= 4; index++) {
    const y = plot.y + plot.h * index / 4
    ctx.beginPath(); ctx.moveTo(plot.x, y); ctx.lineTo(plot.x + plot.w, y); ctx.stroke()
  }
  ctx.fillStyle = colors.text
  ctx.font = '11px sans-serif'
  ctx.fillText(title, pad.left, 14)
  ctx.fillStyle = colors.axis
  ctx.fillText(xLabel || '', plot.x, height - 8)
  ctx.save(); ctx.translate(10, plot.y + plot.h / 2); ctx.rotate(-Math.PI / 2)
  ctx.fillText(yLabel || '', 0, 0); ctx.restore()
  return plot
}

const scale = (value, range, plot, vertical) => {
  const ratio = (value - range.low) / (range.high - range.low)
  return vertical ? plot.y + plot.h * (1 - ratio) : plot.x + plot.w * ratio
}

const empty = (ctx, width, height) => {
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = colors.axis; ctx.font = '12px sans-serif'
  ctx.fillText('暂无可绘制的真实数据', 16, height / 2)
}

function drawHistory(ctx, plot, series) {
  const history = series.history || []
  const range = bounds(history.flatMap(item => [item.value, item.best]))
  if (!range) return false
  ctx.strokeStyle = colors.primary
  ctx.lineWidth = 2
  ctx.beginPath()
  let started = false
  history.forEach((item, index) => {
    if (item.best === null) return
    const x = plot.x + plot.w * (index / Math.max(1, history.length - 1))
    const y = scale(item.best, range, plot, true)
    if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true }
  })
  ctx.stroke()
  ctx.fillStyle = colors.muted
  history.forEach((item, index) => {
    if (item.value === null) return
    const x = plot.x + plot.w * (index / Math.max(1, history.length - 1))
    ctx.beginPath(); ctx.arc(x, scale(item.value, range, plot, true), 2, 0, Math.PI * 2); ctx.fill()
  })
  ctx.fillStyle = colors.axis; ctx.font = '10px sans-serif'
  ctx.fillText(range.low.toFixed(3), 2, plot.y + plot.h)
  ctx.fillText(range.high.toFixed(3), 2, plot.y + 10)
  return true
}

function drawDistribution(ctx, plot, series) {
  const bins = (series.distribution || {}).bins || []
  if (!bins.length) return false
  const top = Math.max(...bins.map(item => item.count), 1)
  const width = plot.w / bins.length
  ctx.fillStyle = colors.fill
  ctx.strokeStyle = colors.primary
  bins.forEach((item, index) => {
    const height = plot.h * item.count / top
    const x = plot.x + index * width + 1
    ctx.fillRect(x, plot.y + plot.h - height, Math.max(1, width - 2), height)
    ctx.strokeRect(x, plot.y + plot.h - height, Math.max(1, width - 2), height)
  })
  return true
}

function drawScatter(ctx, plot, series, param) {
  const points = ((series.scatter || []).find(item => item.param === param) || {}).points || []
  if (!points.length) return false
  const xRange = bounds(points.map(item => item.x))
  const yRange = bounds(points.map(item => item.y))
  if (!xRange || !yRange) return false
  points.forEach(item => {
    ctx.fillStyle = item.status === 'completed' ? colors.primary : colors.muted
    ctx.beginPath()
    ctx.arc(scale(item.x, xRange, plot, false), scale(item.y, yRange, plot, true), 2.5, 0, Math.PI * 2)
    ctx.fill()
  })
  ctx.fillStyle = colors.axis; ctx.font = '10px sans-serif'
  ctx.fillText(xRange.low.toFixed(3), plot.x, heightBottom(plot))
  ctx.fillText(xRange.high.toFixed(3), plot.x + plot.w - 30, heightBottom(plot))
  return true
}

const heightBottom = plot => plot.y + plot.h + 14

function drawParallel(ctx, plot, series) {
  const parallel = series.parallel || {}
  const names = parallel.params || []
  const rows = parallel.rows || []
  if (names.length < 2 || !rows.length) return false
  ctx.lineWidth = 1
  rows.forEach(row => {
    ctx.strokeStyle = row.status === 'completed' ? 'rgba(37, 99, 235, 0.35)' : 'rgba(148, 163, 184, 0.5)'
    ctx.beginPath()
    row.values.forEach((value, index) => {
      if (value === null) return
      const x = plot.x + plot.w * index / (names.length - 1)
      const y = plot.y + plot.h * (1 - value)
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
    })
    ctx.stroke()
  })
  ctx.fillStyle = colors.text; ctx.font = '9px sans-serif'
  names.forEach((name, index) => {
    ctx.save()
    ctx.translate(plot.x + plot.w * index / (names.length - 1), plot.y + plot.h + 12)
    ctx.rotate(-Math.PI / 4)
    ctx.fillText(name.slice(0, 14), 0, 0)
    ctx.restore()
  })
  return true
}

function drawStatus(ctx, plot, series) {
  const items = series.status || []
  if (!items.length) return false
  const top = Math.max(...items.map(item => item.count), 1)
  const height = plot.h / items.length
  items.forEach((item, index) => {
    const y = plot.y + index * height
    ctx.fillStyle = colors.fill
    ctx.fillRect(plot.x, y + 2, plot.w * item.count / top, Math.max(4, height - 8))
    ctx.fillStyle = colors.text; ctx.font = '11px sans-serif'
    ctx.fillText(item.status + ' · ' + item.count, plot.x + 4, y + height / 2)
  })
  return true
}

// The canvas node appears one render tick after setData; retry a few times.
async function withContext(page, id, painter) {
  let lastError
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const { ctx, width, height } = await getContext(page, id)
      painter(ctx, width, height)
      return
    } catch (error) {
      lastError = error
      await new Promise(resolve => setTimeout(resolve, 90))
    }
  }
  throw lastError
}

function draw(page, id, kind, series, param) {
  const titles = {
    history: ['累计最优（完整任务序列）', '试验顺序', '目标值'],
    distribution: ['目标值分布（当前筛选）', '目标区间', '试验数'],
    scatter: ['参数与目标（当前筛选）', param || '参数', '目标值'],
    parallel: ['参数平行坐标（当前筛选）', '参数轴', '归一化值'],
    status: ['试验状态分布', '数量', '状态']
  }
  const painters = { history: drawHistory, distribution: drawDistribution,
    scatter: drawScatter, parallel: drawParallel, status: drawStatus }
  return withContext(page, id, (ctx, width, height) => {
    const [title, xLabel, yLabel] = titles[kind] || [kind, '', '']
    const plot = frame(ctx, width, height, title, xLabel, yLabel)
    const drawn = painters[kind] ? painters[kind](ctx, plot, series || {}, param) : false
    if (!drawn) empty(ctx, width, height)
  })
}

function drawRing(page, id, segments) {
  return withContext(page, id, (ctx, width, height) => {
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, width, height)
    const total = segments.reduce((sum, item) => sum + item.count, 0)
    const cx = width / 2, cy = height / 2
    const radius = Math.min(cx, cy) - 12
    if (!total || radius <= 0) return
    const line = Math.max(10, Math.round(radius * 0.4))
    ctx.lineWidth = line
    ctx.strokeStyle = '#eef2f7'
    ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.stroke()
    let start = -Math.PI / 2
    segments.forEach(item => {
      const angle = 2 * Math.PI * item.count / total
      ctx.strokeStyle = item.color
      ctx.beginPath(); ctx.arc(cx, cy, radius, start, start + angle); ctx.stroke()
      start += angle
    })
    ctx.textAlign = 'center'
    ctx.fillStyle = '#0C447C'
    ctx.font = '500 16px sans-serif'
    ctx.fillText(String(total), cx, cy + 5)
    ctx.fillStyle = colors.axis
    ctx.font = '11px sans-serif'
    ctx.fillText('试验', cx, cy + 21)
  })
}

function drawCurve(page, id, points) {
  return withContext(page, id, (ctx, width, height) => {
    const plot = frame(ctx, width, height, '训练曲线采样 · 真实采样点', '迭代', '损失')
    const values = []
    points.forEach(point => {
      if (point.train !== null) values.push(point.train)
      if (point.validation !== null) values.push(point.validation)
    })
    if (!values.length) { empty(ctx, width, height); return }
    let low = Math.min(...values), high = Math.max(...values)
    if (low === high) { low -= 1; high += 1 }
    const x = index => plot.x + plot.w * (index / Math.max(1, points.length - 1))
    const y = value => plot.y + plot.h * (1 - (value - low) / (high - low))
    const line = (key, color) => {
      ctx.strokeStyle = color
      ctx.lineWidth = 2
      ctx.beginPath()
      let started = false
      points.forEach((point, index) => {
        if (point[key] === null) return
        if (started) ctx.lineTo(x(index), y(point[key]))
        else { ctx.moveTo(x(index), y(point[key])); started = true }
      })
      ctx.stroke()
      ctx.fillStyle = color
      points.forEach((point, index) => {
        if (point[key] === null) return
        ctx.beginPath(); ctx.arc(x(index), y(point[key]), 2.5, 0, Math.PI * 2); ctx.fill()
      })
    }
    line('train', colors.primary)
    line('validation', '#d97706')
    ctx.fillStyle = colors.axis
    ctx.font = '10px sans-serif'
    ctx.fillText(low.toFixed(3), 2, plot.y + plot.h)
    ctx.fillText(high.toFixed(3), 2, plot.y + 10)
  })
}

// Cache the canvas once, then repaint on every tick instead of re-querying the node.
async function context(page, id) {
  return getContext(page, id)
}

function paintWave(view, wave, index) {
  const { ctx, width, height } = view
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  const points = (wave && wave.points) || []
  const channels = (wave && wave.channels) || []
  if (!points.length || !channels.length) { empty(ctx, width, height); return }
  const plot = { x: 38, y: 18, w: width - 52, h: height - 40 }
  const values = []
  points.forEach(point => point.values.forEach(value => {
    if (typeof value === 'number' && isFinite(value)) values.push(value)
  }))
  if (!values.length) { empty(ctx, width, height); return }
  let low = Math.min(...values), high = Math.max(...values)
  if (low === high) { low -= 1; high += 1 }
  ctx.strokeStyle = colors.grid
  ctx.lineWidth = 1
  for (let line = 0; line <= 4; line++) {
    const y = plot.y + plot.h * line / 4
    ctx.beginPath(); ctx.moveTo(plot.x, y); ctx.lineTo(plot.x + plot.w, y); ctx.stroke()
  }
  const x = i => plot.x + plot.w * (i / Math.max(1, points.length - 1))
  const y = value => plot.y + plot.h * (1 - (value - low) / (high - low))
  const shown = Math.max(1, Math.min(index || 1, points.length))
  channels.forEach((channel, ci) => {
    ctx.strokeStyle = channel.color
    ctx.lineWidth = 2
    ctx.beginPath()
    let started = false
    for (let i = 0; i < shown; i++) {
      const value = points[i].values[ci]
      if (typeof value !== 'number' || !isFinite(value)) continue
      if (started) ctx.lineTo(x(i), y(value)); else { ctx.moveTo(x(i), y(value)); started = true }
    }
    ctx.stroke()
    const last = points[shown - 1].values[ci]
    if (typeof last === 'number' && isFinite(last)) {
      ctx.fillStyle = channel.color
      ctx.beginPath(); ctx.arc(x(shown - 1), y(last), 3, 0, Math.PI * 2); ctx.fill()
    }
  })
  const scanX = x(shown - 1)
  ctx.strokeStyle = colors.axis
  ctx.lineWidth = 1
  if (ctx.setLineDash) ctx.setLineDash([3, 3])
  ctx.beginPath(); ctx.moveTo(scanX, plot.y); ctx.lineTo(scanX, plot.y + plot.h); ctx.stroke()
  if (ctx.setLineDash) ctx.setLineDash([])
  ctx.fillStyle = colors.axis
  ctx.font = '10px sans-serif'
  ctx.fillText(high.toFixed(3), 2, plot.y + 8)
  ctx.fillText(low.toFixed(3), 2, plot.y + plot.h)
}

function drawLoss(page, id, losses) {
  return withContext(page, id, (ctx, width, height) => {
    const plot = frame(ctx, width, height, 'LoRA SFT loss 曲线', '训练轮次', 'loss')
    const points = (losses || []).filter(value => typeof value === 'number' && isFinite(value))
    if (!points.length) { empty(ctx, width, height); return }
    const range = bounds(points)
    ctx.strokeStyle = colors.primary
    ctx.lineWidth = 2
    ctx.beginPath()
    points.forEach((value, index) => {
      const x = plot.x + plot.w * (index / Math.max(1, points.length - 1))
      const y = scale(value, range, plot, true)
      if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y)
    })
    ctx.stroke()
    ctx.fillStyle = colors.accent
    points.forEach((value, index) => {
      const x = plot.x + plot.w * (index / Math.max(1, points.length - 1))
      ctx.beginPath(); ctx.arc(x, scale(value, range, plot, true), 2.5, 0, Math.PI * 2); ctx.fill()
    })
    ctx.fillStyle = colors.axis
    ctx.font = '10px sans-serif'
    ctx.fillText(range.high.toFixed(3), 2, plot.y + 9)
    ctx.fillText(range.low.toFixed(3), 2, plot.y + plot.h)
  })
}

module.exports = { draw, drawRing, drawCurve, drawLoss, context, paintWave }
