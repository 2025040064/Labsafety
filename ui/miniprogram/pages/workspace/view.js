// Turn backend payloads into flat rows the WXML templates can render directly.
const { statusText, actionText } = require('./config')

const text = value => {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isFinite(value) ? String(Number(value.toFixed(6))) : '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.map(text).join('、') : '—'
  if (typeof value === 'object') {
    const parts = Object.keys(value).slice(0, 12).map(key => key + '=' + text(value[key]))
    return parts.join('，') + (Object.keys(value).length > 12 ? ' …' : '')
  }
  return String(value)
}

const pretty = (value, limit = 12000) => JSON.stringify(value, (key, item) => {
  if (Array.isArray(item) && item.length > 40) return item.slice(0, 40).concat('其余记录请导出查看')
  return item
}, 2).slice(0, limit)

const section = (title, rows, note) => ({ title, rows: rows.filter(row => row && row.value !== '—'), note: note || '' })
const row = (label, value, note) => ({ label, value: text(value), note: note || '' })
const pairs = (object, prefix = '') => Object.keys(object || {}).map(key =>
  row(prefix + key, object[key]))

const regimeRows = metrics => Object.keys((metrics && metrics.regimes) || {}).map(name => {
  const item = metrics.regimes[name] || {}
  return row(name, [text(item.mae) + ' MAE', text(item.samples) + ' 样本',
    text(item.independent_groups) + ' 独立组', text(item.reliability)].join(' · '))
})

const dataSections = result => {
  if (!result) return []
  const manifest = result.manifest || {}
  const audit = result.quality_audit || {}
  return [
    section('数据概览', [row('数据集', result.dataset_id), row('记录数', result.row_count),
      row('预测目标', result.target), row('目标单位', result.target_unit)]),
    section('固定划分', pairs(result.split_counts)),
    section('预定义工况', pairs(result.regimes)),
    section('字段及单位', [row('特征数', (result.features || []).length),
      row('特征', (result.features || []).join('、'))]),
    section('来源与许可', [row('来源', manifest.source || result.source_path),
      row('许可', manifest.license), row('分组定义', manifest.group_definition),
      row('数据模式', manifest.data_mode)]),
    section('SHA-256', [row('源文件', result.source_sha256)]),
    section('质量审计', pairs(audit), '质量审计只报告检查结果，不修改原始数据。'),
    section('清单与限制', pairs(manifest).concat(
      (result.limitations || []).map((item, index) => row('限制 ' + (index + 1), item)))),
  ]
}

const metricsRows = (metrics, unit) => {
  const keys = ['overall_mae', 'overall_error', 'accuracy', 'balanced_accuracy', 'precision',
    'recall', 'f1', 'false_negative_rate', 'false_positive_rate', 'robust_objective', 'overall_rmse']
  const rows = keys.filter(key => metrics && metrics[key] !== undefined)
    .map(key => row(key, metrics[key], key.startsWith('overall_mae') ? unit : ''))
  return rows.concat(regimeRows(metrics))
}

const commonSections = detail => {
  const config = detail.config || {}
  return [
    section('任务与配置', [row('实验 ID', detail.id), row('算法', detail.algorithm),
      row('状态', statusText[detail.status] || detail.status), row('数据集', detail.dataset_id || detail.dataset_key),
      row('目标单位', detail.target_unit), row('随机种子', config.experiment ? config.experiment.seed : config.seed),
      row('最大树数', config.model ? config.model.n_estimators : (config.model_config || {}).n_estimators),
      row('线程数', config.model ? config.model.n_jobs : (config.model_config || {}).n_jobs),
      row('试验上限', config.optimization ? config.optimization.max_trials : config.max_trials),
      row('时间预算（秒）', config.optimization ? config.optimization.timeout_seconds : config.timeout_seconds)]),
    section('证据与版本', [row('数据 SHA-256', detail.data_sha256),
      row('应用版本', detail.app_version), row('算法版本', detail.algorithm_version),
      row('Schema', detail.schema_version), row('划分指纹', (detail.manifest || {}).split_fingerprint),
      row('分组定义', (detail.manifest || {}).group_definition)]),
    section('耗时', [row('总耗时（秒）', detail.duration_seconds),
      row('基线耗时（秒）', detail.baseline_duration_seconds),
      row('搜索耗时（秒）', detail.search_duration_seconds)]),
  ]
}

const trainSections = detail => [
  section('验证结果', [row('验证 MAE', (detail.metrics || {}).overall_mae, detail.target_unit),
    row('固定 J（无量纲）', (detail.metrics || {}).robust_objective),
    row('总耗时（秒）', detail.duration_seconds), row('最佳迭代', detail.best_iteration)]),
  section('分工况样本及 MAE', regimeRows(detail.metrics)),
  section('实际模型参数', pairs(detail.best_params || {}, 'best · ').concat(
    pairs((detail.config || {}).model || {}, 'model · '))),
  ...commonSections(detail),
]

const optimizeSections = detail => {
  const baseline = detail.baseline || {}
  const series = detail.charts || {}
  return [
    section('搜索结果', [row('目标', series.objective_label),
      row('最佳目标值', detail.best_value), row('最佳试验', detail.best_trial),
      row('试验数（不含基线）', detail.trial_count), row('状态', statusText[detail.status] || detail.status)]),
    section('默认基线（试验 -1）', [row('状态', statusText[baseline.status] || baseline.status),
      row('验证 MAE', baseline.mae, detail.target_unit), row('目标值', baseline.objective),
      row('耗时（秒）', baseline.duration)], '默认基线不参与搜索排名。'),
    section('最佳参数', pairs(detail.best_params || {})),
    section('搜索诊断', pairs(detail.search_diagnostics || {}).concat(
      [row('失败原因', detail.failure_reason)]),
      '代理树离散度与前向残差重采样只作未校准的搜索近似；最终 J 固定。'),
    section('状态分布', (series.status || []).map(item =>
      row(statusText[item.status] || item.status, item.count))),
    ...commonSections(detail),
  ]
}

const industrialSections = detail => {
  const artifacts = detail.artifacts || {}
  const curve = (detail.metrics || {}).learning_curve || []
  const steps = curve.length ? Array.from({ length: Math.min(6, curve.length) }, (_, index) =>
    Math.round(index * (curve.length - 1) / Math.max(1, Math.min(6, curve.length) - 1))) : []
  const samples = steps.map(index => {
    const item = curve[index] || {}
    return row('迭代 ' + text(item.iteration),
      ['训练损失 ' + text(item.train_loss), '验证损失 ' + text(item.validation_loss)].join(' · '))
  })
  return [
    section('数据审计', [row('数据集', detail.dataset_key), row('任务类型', detail.task_type),
      row('数据 SHA-256', detail.data_sha256), row('质量审计', detail.quality_audit),
      row('划分', (detail.manifest || {}).split_counts)]),
    section('分类指标', metricsRows(detail.metrics, detail.target_unit),
      detail.objective_definition || '分类指标与回归指标分别呈现，不混用单位。'),
    section('各工况', regimeRows(detail.metrics)),
    section('搜索记录', [row('已保存试验', detail.trial_count), row('最佳试验', detail.best_trial),
      row('最佳目标', detail.best_value), row('搜索耗时（秒）', detail.search_duration_seconds),
      row('最佳参数', detail.best_params)]),
    section('训练曲线采样', samples, '只显示真实记录的采样点；完整曲线由 ZIP 导出，缺失时显示空态。'),
    section('曲线与产物', [row('收敛曲线', artifacts.convergence),
      row('训练曲线', artifacts.learning_curve), row('模型', artifacts.model),
      row('日志', artifacts.log), row('模型 SHA-256', detail.model_sha256)],
      '曲线文件由 ZIP 导出，小程序显示指标与记录。'),
    ...commonSections(detail),
  ]
}

const loopSections = session => {
  const policy = session.policy || {}
  const gate = session.gate || {}
  const events = session.events || []
  return [
    section('会话状态', [row('会话 ID', session.id), row('状态', session.state),
      row('修订号', session.revision), row('数据模式', session.data_mode),
      row('HPO 次数', session.hpo_attempts), row('训练用时（秒）', session.training_seconds),
      row('回放位置', session.batch_offset)]),
    section('策略与预算', [row('场景', policy.scenario), row('算法', policy.algorithm),
      row('试验上限', policy.max_trials), row('搜索预算（秒）', policy.timeout_seconds),
      row('训练总预算（秒）', policy.training_budget_seconds), row('固定种子', policy.seed)],
      '未经授权不运行训练与搜索；候选通过门禁后仍需人工确认切换。'),
    section('诊断与门禁', pairs(gate).concat([row('提案', session.proposal),
      row('门禁结果', gate.result)])),
    section('模型与参数', [row('冠军', session.champion), row('候选', session.candidate),
      row('活动模型', session.active_model), row('预测数', (session.predictions || []).length)]),
    section('数据划分', pairs(session.protocol || {}, 'protocol · ')),
    section('审计时间轴', events.slice(-12).map((event, index) =>
      row(String(event.sequence || index + 1),
        [event.kind || event.type, event.actor, event.timestamp].filter(Boolean).join(' · '))),
      '仅显示最近 12 条事件，完整时间轴请导出会话 ZIP。'),
  ]
}

const jobSections = job => [
  section('任务', [row('任务 ID', job.id),
    row('动作', actionText[job.action] || job.action),
    row('状态', statusText[job.status] || job.status), row('错误', job.error)]),
  section('进度', [row('最新事件', job.progress)]),
]

const llmSections = detail => {
  const losses = detail.losses || []
  const generated = detail.generated || {}
  return [
    section('训练配置', [row('运行 ID', detail.id), row('基座模型', detail.base_model),
      row('状态', detail.status), row('LoRA 秩', detail.lora_rank),
      row('迭代数', detail.iterations), row('学习率', detail.learning_rate),
      row('样本数', (detail.examples || []).length)]),
    section('Loss 曲线', losses.map((value, index) => row('第 ' + (index + 1) + ' 轮', value)),
      '每轮真实记录的加权交叉熵，越大表示拟合越差。'),
    section('采样对比', [row('Prompt', detail.sample_prompt),
      row('基座模型输出', generated.base), row('SFT 后输出', generated.sft)],
      '同一 prompt、temperature=0、seed=42 的训练前后对比。'),
    section('产物', [row('权重路径', detail.weights_path), row('结果目录', detail.directory),
      row('完成时间', detail.created_at)]),
  ]
}

const compareView = result => {
  const runs = result.runs || []
  const verdict = result.reason || (result.comparable ? '证据一致，可公平比较' :
    '证据不足，只并排展示，不生成排名。')
  const fields = [
    ['实验 ID', item => item.id], ['数据集', item => item.dataset],
    ['算法', item => item.algorithm], ['模型', item => item.model],
    ['状态', item => statusText[item.status] || item.status],
    ['最佳目标', item => item.best_value], ['目标单位', item => item.target_unit],
    ['目标定义', item => item.objective_definition],
    ['随机种子', item => item.seed], ['试验上限', item => item.max_trials],
    ['时间预算（秒）', item => item.timeout_seconds],
    ['验证 MAE', item => (item.metrics || {}).overall_mae],
    ['固定 J', item => (item.metrics || {}).robust_objective],
    ['分类错误', item => (item.metrics || {}).overall_error],
    ['准确率', item => (item.metrics || {}).accuracy],
    ['数据 SHA-256', item => item.data_sha256],
    ['划分指纹/样本', item => item.group_definition || item.split_counts],
    ['应用版本', item => item.app_version], ['算法版本', item => item.algorithm_version],
    ['最佳参数', item => item.best_params],
  ]
  return {
    columns: fields.map(([label, read]) => ({ label, values: runs.map(item => text(read(item))) })),
    ids: runs.map(item => item.id),
    count: runs.map((_, index) => index),
    note: verdict + (result.fair_hpo_comparable === false ? '；含基线，不构成同预算 HPO 排名。' : ''),
    ranking: Boolean(result.ranking)
  }
}

const trialRows = (rows, unit, objectiveLabel) => rows.map(item => ({
  number: item.number,
  status: statusText[item.status] || item.status,
  objective: text(item.objective),
  mae: item.mae === null && item.error !== null ? text(item.error) : text(item.mae),
  maeLabel: item.mae === null && item.error !== null ? '分类错误' : '验证 MAE',
  unit: unit || '',
  duration: text(item.duration),
  params: Object.keys(item.params || {}).map(key => key + '=' + text(item.params[key])).join('  '),
  selection: item.selection || '',
  failure: item.failure_reason || '',
  objectiveLabel: objectiveLabel || '目标'
}))

const logRows = events => events.map((event, index) => ({
  id: 'log-' + index,
  time: String(event.timestamp || '').slice(11, 19) || '—',
  type: event.type || event.kind || 'event',
  message: event.message || event.failure_reason || event.status || '',
  detail: pretty(event, 400)
})).reverse()

const runRows = runs => runs.map(item => ({
  id: item.id,
  caption: [item.dataset_id || item.dataset_key, item.algorithm,
    statusText[item.status] || item.status].filter(Boolean).join(' · '),
  value: text(item.best_value),
  unit: item.target_unit || ''
}))

// Chart view models. Percentages, labels and class names are computed here because
// WXML expressions cannot call JS methods.
const isNumber = value => typeof value === 'number' && Number.isFinite(value)
const percent = (value, max) => max > 0 ? Math.max(2, Math.round(value / max * 100)) : 0

const STATUS_COLORS = {
  completed: { color: '#2563eb', className: 'dot-completed' },
  failed: { color: '#dc2626', className: 'dot-failed' },
  pruned: { color: '#d97706', className: 'dot-pruned' },
  cancelled: { color: '#7c3aed', className: 'dot-cancelled' }
}
const OTHER_STATUS = { color: '#94a3b8', className: 'dot-other' }
const SPLIT_LABELS = { train: '训练', validation: '验证', test: '测试' }
const INDICATORS = [
  ['overall_mae', '验证 MAE', 'unit'],
  ['robust_objective', '固定 J', ''],
  ['normalized_regime_mae', '工况 MAE', ''],
  ['overall_f1_macro', '宏平均 F1', ''],
  ['overall_balanced_accuracy', '平衡准确率', ''],
  ['overall_error', '分类错误', ''],
  ['overall_average_precision', '平均精度', '']
]

const emptyVisual = () => ({
  any: false, kpi: [], indicators: [],
  regimes: { title: '', unit: '', note: '', rows: [] },
  split: { total: '', note: '', rows: [] },
  ring: { total: 0, note: '', segments: [] },
  curve: { note: '', points: [] },
  process: { note: '', stages: [] },
  flow: { note: '', rows: [] },
  wave: { title: '', note: '', channels: [], points: [] }
})

const dataFlow = (detail, result) => {
  const d = detail || result || {}
  const m = d.manifest || {}
  const audit = d.quality_audit || {}
  const split = m.split_summary || d.split_counts || {}
  const total = isNumber(d.row_count) ? d.row_count : (isNumber(m.rows) ? m.rows : null)
  const rows = []
  if (isNumber(total)) rows.push({ label: '原始', value: total, className: 'flow-raw' })
  if (isNumber(audit.audited_rows)) rows.push({ label: '已审计', value: audit.audited_rows, className: 'flow-audit' })
  for (const [key, label, className] of [['train', '训练', 'flow-train'],
    ['validation', '验证', 'flow-valid'], ['test', '测试', 'flow-test']]) {
    const item = split[key]
    const value = isNumber(item) ? item : (item && isNumber(item.rows) ? item.rows : null)
    if (value !== null) rows.push({ label, value, className })
  }
  if (!rows.length) return { note: '', rows: [] }
  const max = Math.max(...rows.map(item => item.value))
  return {
    note: '灰色为封存测试集，只在显式最终评估读取。',
    rows: rows.map(item => ({ label: item.label, value: text(item.value),
      pct: percent(item.value, max), className: item.className }))
  }
}

const waveSeries = (detail, series) => {
  const curve = ((detail || {}).metrics || {}).learning_curve
  if (Array.isArray(curve) && curve.length) {
    const points = curve.filter(item => isNumber(item.iteration)).map(item => ({
      label: text(item.iteration),
      values: [isNumber(item.train_loss) ? item.train_loss : null,
        isNumber(item.validation_loss) ? item.validation_loss : null]
    }))
    if (points.length) {
      return {
        title: '训练曲线', note: '逐点回放真实采样点；完整曲线由 ZIP 导出。',
        channels: [{ key: 'train', label: '训练损失', color: '#2563eb' },
          { key: 'validation', label: '验证损失', color: '#BA7517' }],
        points
      }
    }
  }
  const history = (series || {}).history || []
  const points = history.filter(item => item.number >= 0).map(item => ({
    label: String(item.number),
    values: [isNumber(item.value) ? item.value : null, isNumber(item.best) ? item.best : null]
  }))
  if (!points.length) return { title: '', note: '', channels: [], points: [] }
  return {
    title: '目标值序列', note: '逐点回放 /charts 的真实试验序列；筛选不改变波形数据。',
    channels: [{ key: 'trial', label: '单次试验', color: '#85B7EB' },
      { key: 'best', label: '累计最优', color: '#2563eb' }],
    points
  }
}

const kpiCards = detail => {
  const metrics = detail.metrics || {}
  const classification = isNumber(metrics.overall_error) || isNumber(metrics.overall_f1_macro)
  return [
    { label: '最佳目标', value: text(detail.best_value), unit: '' },
    { label: '试验数', value: text(detail.trial_count), unit: '条' },
    { label: classification ? '分类错误' : '验证 MAE',
      value: text(classification ? metrics.overall_error : metrics.overall_mae),
      unit: classification ? '' : (detail.target_unit || '') }
  ]
}

const indicatorCards = detail => {
  const metrics = detail.metrics || {}
  return INDICATORS.filter(([key]) => isNumber(metrics[key])).slice(0, 4)
    .map(([key, label, unit]) => ({ label, value: text(metrics[key]),
      unit: unit === 'unit' ? (detail.target_unit || '') : '' }))
}

const regimeBars = detail => {
  const regimes = (detail.metrics || {}).regimes || detail.regimes || {}
  const names = Object.keys(regimes)
  if (names.length && names.every(name => isNumber(regimes[name]))) {
    const max = Math.max(...names.map(name => regimes[name]))
    return {
      title: '工况样本分布', unit: '条',
      note: '来自数据预定义的工况样本数；不据此生成排名。',
      rows: names.map(name => ({ label: name, value: text(regimes[name]),
        pct: percent(regimes[name], max) }))
    }
  }
  const regression = names.some(name => isNumber((regimes[name] || {}).mae))
  const key = regression ? 'mae' : 'error'
  const rows = names.map(name => ({ name, value: (regimes[name] || {})[key] }))
    .filter(item => isNumber(item.value))
  if (!rows.length) return { title: '', unit: '', note: '', rows: [] }
  const max = Math.max(...rows.map(item => item.value))
  return {
    title: regression ? '分工况 MAE' : '分工况分类错误',
    unit: regression ? (detail.target_unit || '') : '',
    note: '工况来自数据预定义；不据此生成排名。',
    rows: rows.map(item => ({ label: item.name, value: text(item.value),
      pct: percent(item.value, max) }))
  }
}

const splitBar = (detail, result) => {
  const summary = ((detail || {}).manifest || {}).split_summary
  const raw = summary || (result || {}).split_counts || (detail || {}).split_counts
  if (!raw || typeof raw !== 'object') return { total: '', note: '', rows: [] }
  const counts = {}
  for (const key of Object.keys(SPLIT_LABELS)) {
    const item = raw[key]
    const value = isNumber(item) ? item : (item && isNumber(item.rows) ? item.rows : null)
    if (value !== null && value > 0) counts[key] = value
  }
  const keys = Object.keys(counts)
  const total = keys.reduce((sum, key) => sum + counts[key], 0)
  if (!total) return { total: '', note: '', rows: [] }
  return {
    total: text(total),
    note: '来自固定划分记录；测试集只在显式最终评估读取。',
    rows: keys.map(key => ({ key, label: SPLIT_LABELS[key], count: text(counts[key]),
      pct: Math.round(counts[key] / total * 100), className: 'seg-' + key }))
  }
}

const statusRing = series => {
  const items = ((series || {}).status || []).filter(item => item.count > 0)
  const total = items.reduce((sum, item) => sum + item.count, 0)
  if (!total) return { total: 0, note: '', segments: [] }
  return {
    total,
    note: '按任务全量统计；筛选不改变状态计数。',
    segments: items.map(item => {
      const style = STATUS_COLORS[item.status] || OTHER_STATUS
      return { label: statusText[item.status] || item.status, count: item.count,
        pct: Math.round(item.count / total * 100),
        color: style.color, className: style.className }
    })
  }
}

const curvePoints = detail => {
  const curve = (detail.metrics || {}).learning_curve
  if (!Array.isArray(curve)) return { note: '', points: [] }
  const points = curve.filter(item => isNumber(item.iteration) &&
    (isNumber(item.train_loss) || isNumber(item.validation_loss))).map(item => ({
      iteration: item.iteration,
      train: isNumber(item.train_loss) ? item.train_loss : null,
      validation: isNumber(item.validation_loss) ? item.validation_loss : null
    }))
  return { note: '只绘制真实记录的采样点；完整曲线由 ZIP 导出。', points }
}

// Data processing flow: icons backed by real manifest / audit / split fields.
const shortSha = value => typeof value === 'string' && value.length > 12 ? value.slice(0, 8) + '…' : ''

const processStage = (key, name, icon, summary) => ({
  key, name, icon, summary: summary || '未提供',
  state: summary ? '已完成' : '未提供',
  stateClass: summary ? '' : 'muted',
  className: summary ? 'badge-done' : 'badge-muted'
})

const splitSummaryText = source => {
  const raw = (source.manifest || {}).split_summary || source.split_counts || null
  if (!raw) return ''
  const parts = []
  for (const [key, label] of [['train', '训练'], ['validation', '验证'], ['test', '测试']]) {
    const item = raw[key]
    const value = isNumber(item) ? item : (item && isNumber(item.rows) ? item.rows : null)
    if (value !== null) parts.push(label + ' ' + text(value))
  }
  if (!parts.length) return ''
  const fingerprint = (source.manifest || {}).split_fingerprint
  return parts.join(' / ') + (fingerprint ? ' · 指纹 ' + shortSha(fingerprint) : '')
}

const processStages = (detail, result) => {
  const d = detail || result || {}
  const m = d.manifest || {}
  const metrics = d.metrics || {}
  const audit = d.quality_audit || {}
  const records = d.row_count || m.rows || metrics.samples
  const regimeNames = Object.keys(metrics.regimes || d.regimes || {})
  const features = d.features || m.feature_order || []
  const unit = d.target_unit || m.target_unit || ''
  const sha = d.source_sha256 || d.data_sha256 || m.source_sha256
  const flagged = Array.isArray(audit.flagged_source_rows) ? audit.flagged_source_rows.length : 0
  const auditText = [isNumber(audit.audited_rows) ? text(audit.audited_rows) + ' 行已审计' : '',
    flagged ? flagged + ' 行标记（只标记不删行）' : ''].filter(Boolean).join(' · ') ||
    (d.quality_audit ? '审计记录已保存' : '')
  const quality = isNumber(metrics.overall_mae) ? '验证 MAE ' + text(metrics.overall_mae) + (unit ? ' ' + unit : '') :
    (isNumber(metrics.overall_error) ? '分类错误 ' + text(metrics.overall_error) :
      (isNumber(metrics.overall_f1_macro) ? '宏平均 F1 ' + text(metrics.overall_f1_macro) : ''))
  const stages = [
    processStage('source', '数据接入', '源',
      [d.dataset_id || d.dataset_key || m.dataset_id,
        isNumber(records) ? text(records) + ' 条记录' : ''].filter(Boolean).join(' · ')),
    processStage('sha', '来源校验', '验',
      [shortSha(sha), m.provenance_verified === true ? '来源已核验' : ''].filter(Boolean).join(' · ')),
    processStage('audit', '质量审计', '审', auditText),
    processStage('split', '固定划分', '分', splitSummaryText(d)),
    processStage('regime', '工况与特征', '况',
      [regimeNames.length ? regimeNames.length + ' 个工况' : '',
        features.length ? features.length + ' 个特征' : '',
        unit ? '目标单位 ' + unit : ''].filter(Boolean).join(' · ') || m.regime_definition || ''),
    processStage('metric', '验证结果', '果', quality)
  ]
  if (!stages.some(item => item.state === '已完成')) return { note: '', stages: [] }
  stages.forEach((item, index) => { item.last = index === stages.length - 1 })
  return {
    note: '测试集保持封存，只在显式「最终评估」读取；完整清单与产物由 ZIP 导出。缺失字段显示「未提供」，不补全。',
    stages
  }
}

const visuals = (detail, series, result) => {
  const data = emptyVisual()
  data.split = splitBar(detail, result)
  data.process = processStages(detail, result)
  data.flow = dataFlow(detail, result)
  data.wave = waveSeries(detail, series)
  const id = String((detail || {}).id || '')
  if (!id.startsWith('EXP-') && !id.startsWith('IND-')) {
    data.regimes = regimeBars(result || {})
    data.any = data.split.rows.length > 0 || data.regimes.rows.length > 0 ||
      data.process.stages.some(item => item.state === '已完成')
    return data
  }
  data.kpi = kpiCards(detail)
  data.indicators = indicatorCards(detail)
  data.regimes = regimeBars(detail)
  data.ring = statusRing(series)
  data.curve = curvePoints(detail)
  data.any = true
  return data
}

const compareBars = result => {
  const runs = (result || {}).runs || []
  const units = runs.map(item => item.target_unit || '')
  const values = runs.map(item => item.best_value)
  const comparable = runs.length >= 2 && units.every(unit => unit === units[0]) &&
    values.every(isNumber) && Math.max(...values) > 0
  if (!comparable) {
    return { unit: '', note: '单位不一致或缺少最佳目标值，只并排展示，不绘制柱状图与排名。', rows: [] }
  }
  const max = Math.max(...values)
  return {
    unit: units[0],
    note: '按输入顺序并排展示，不生成排名。',
    rows: runs.map(item => ({ label: item.id,
      short: String(item.id).replace(/^(EXP|IND|LOOP)-/, '').slice(0, 10),
      value: text(item.best_value),
      height: Math.max(4, Math.round(80 * item.best_value / max)) }))
  }
}

module.exports = {
  text, pretty, section, row, pairs, dataSections, trainSections, optimizeSections,
  industrialSections, loopSections, jobSections, llmSections, compareView, trialRows, logRows, runRows,
  visuals, compareBars
}
