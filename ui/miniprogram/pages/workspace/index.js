const { request, base, localHeaders, setMode, getMode, isDemo, probe } = require('../../api')
const C = require('./config')
const V = require('./view')
const Charts = require('./charts')

const post = (path, data) => request(path, { method: 'POST', data })
const buildQuery = params => Object.keys(params)
  .filter(key => params[key] !== '' && params[key] !== null && params[key] !== undefined)
  .map(key => encodeURIComponent(key) + '=' + encodeURIComponent(params[key])).join('&')

const initialConfig = () => JSON.parse(JSON.stringify(C.pageConfig))

Page({
  data: {
    tabs: C.tabs, tasks: C.tasks, task: 0, runChoices: [], recentRuns: [],
    dashboard: null, datasets: [], datasetPreview: null, datasetPreviewId: '',
    datasetPreviewError: '', industrialAdvanced: false,
    regression: C.regression, industrial: C.industrial, methods: C.methods,
    industrialMethods: C.industrialMethods, models: C.models, decisions: C.decisions,
    scenarios: C.scenarios, chartKinds: C.chartKinds,
    llmModels: C.llmModels, llmKey: '', llmStatus: null, llmEvaluate: true,
    demoMode: false, connecting: false, modeLabel: '', notice: '',
    actionText: C.actionText, statusText: C.statusText,
    methodHint: C.methodHints.tpe,
    cfg: initialConfig(),
    tab: 0, role: '', dataset: 0, industrialDataset: 0, method: 2, industrialMethod: 0,
    model: 0, decision: 0, scenario: 0, llmModel: 0, authorize: false, manual: false,
    upload: null, csvInspection: null, csvTarget: '', csvGroup: '', csvFeatures: '',
    error: '', output: '', sections: [], detail: null, isLlmDetail: false,
    resultSummary: [], submittingAction: '', taskFeedback: {},
    active: null, activeResultId: '', freshId: '', viewingId: '', viewingResource: '',
    runs: [], allRuns: [], runQuery: '', jobs: [], loops: [],
    selected: {}, selectedCount: 0, compare: null,
    trials: { rows: [], total: 0, page: 1, size: 20, pages: 1 },
    trialChecked: {}, trialCheckedCount: 0,
    trialQuery: '', trialStatus: '', trialParam: '', trialLow: '', trialHigh: '',
    trialSort: 'number', trialOrder: 'asc',
    statusChoices: [{ value: '', label: '全部状态' }, { value: 'completed', label: '完成' },
      { value: 'failed', label: '失败' }, { value: 'pruned', label: '已剪枝' },
      { value: 'cancelled', label: '已取消' }, { value: 'running', label: '运行中' }],
    sortChoices: [{ value: 'number', label: '试验序号' }, { value: 'objective', label: '目标值' },
      { value: 'mae', label: '验证 MAE' }, { value: 'duration', label: '耗时' },
      { value: 'status', label: '状态' }],
    orderChoices: [{ value: 'asc', label: '升序' }, { value: 'desc', label: '降序' }],
    trialParamChoices: [{ value: '', label: '不按参数范围筛选' }],
    chartParamChoices: [{ value: '', label: '选择参数' }],
    chartKind: 'history', chartSeries: null, chartMeta: null, chartParams: [], chartParam: '',
    visual: V.visuals(null, null, null), compareBars: { rows: [], note: '' },
    wave: { index: 0, running: false, done: false, total: 0, current: '—' },
    logJobId: '', logQuery: '', logRows: [], logTotal: 0, logPage: 1, logSize: 50,
    autoScroll: true, logTarget: ''
  },

  onLoad() { this.watchedJobs = {}; this.connect() },
  onShow() { this.visible = true; this.watch() },
  onHide() { this.visible = false; clearTimeout(this.timer); this.stopWave() },
  onUnload() { this.onHide() },
  onPullDownRefresh() {
    (this.data.role ? this.refresh() : this.connect()).finally(() => wx.stopPullDownRefresh())
  },

  async execute(fn) {
    this.setData({ error: '' })
    try { return await fn() } catch (error) { this.setData({ error: error.message }) }
  },

  async connect(force) {
    this.setData({ error: '', connecting: true })
    let demo
    if (force === 'demo') demo = true
    else if (force === 'real') demo = false
    else demo = !(await probe())
    setMode(demo ? 'demo' : 'real')
    // 演示模式下 role 直接置为 admin，让 refresh 正常填充数据
    const me = demo ? { role: 'admin' } : await request('/api/me').catch(() => ({ role: 'admin' }))
    this.setData({
      role: me.role, demoMode: demo, connecting: false,
      modeLabel: demo ? '演示模式 · 内置真实结果' : '本机引擎在线'
    })
    if (demo) {
      this.setData({ notice: '未检测到本机后端，已自动切换到离线演示模式。所有界面与交互可用，训练为回放预置结果。' })
      wx.showToast({ title: '演示模式', icon: 'none', duration: 1500 })
    }
    await this.refresh()
    this.watch()
  },

  // 手动在演示 / 真实模式之间切换
  switchMode() {
    if (this.data.submittingAction) return
    const force = isDemo() ? 'real' : 'demo'
    this.previewRequest = null
    this.setData({ notice: '', error: '', taskFeedback: {}, datasetPreviewId: '',
      datasetPreview: null, datasetPreviewError: '' })
    this.connect(force)
  },

  switchTab(event) {
    const tab = Number(event.currentTarget.dataset.index)
    const changed = tab !== this.data.tab
    this.setData({ tab, error: '' }, () => {
      if (changed) wx.pageScrollTo({ scrollTop: 0, duration: 0 })
      if (this.data.role) this.refresh()
      else this.connect()
      if (tab === 1) { this.drawChart(); this.drawVisuals(); this.drawLlmLoss() }
      else this.stopWave()
    })
  },

  switchTask(event) {
    const task = Number(event.currentTarget.dataset.index)
    const patch = { task, error: '' }
    if (this.data.upload && this.data.upload.key !== this.keyForTask(task)) {
      patch.upload = null
      patch.csvInspection = null
    }
    this.setData(patch, () => {
      if (task === 5 && !this.data.llmKey && !this.data.llmStatus) this.checkLlm()
    })
  },

  refresh() {
    return this.execute(async () => {
      if (!this.data.role) return
      const tab = this.data.tab
      if (tab === 0) {
        const [dashboard, datasets] = await Promise.all([request('/api/dashboard'), request('/api/datasets')])
        if (this.data.tab === tab) this.setData({ dashboard, datasets })
      }
      if (tab === 1 || tab === 2) {
        const runs = await request('/api/runs')
        if (this.data.tab === tab) {
          this.allRuns = V.runRows(runs)
          this.applyRunFilter()
          if (tab === 1 && !this.data.viewingId && this.data.recentRuns.length && !this.autoOpening) {
            this.autoOpening = true
            this.openDetail(this.data.recentRuns[0].id)
              .finally(() => { this.autoOpening = false })
          }
        }
      }
      if (tab === 2) {
        const [jobs, loops] = await Promise.all([request('/api/jobs'), request('/api/loops')])
        this.setData({
          jobs: jobs.map(job => ({
            id: job.id, caption: (C.actionText[job.action] || job.action) + ' · ' +
              (C.statusText[job.status] || job.status) + (job.error ? ' · ' + job.error : '')
          })),
          loops: loops.map(item => ({
            id: item.id, caption: [item.state, '修订 ' + item.revision, item.data_mode].join(' · ')
          }))
        })
      }
    })
  },

  async showDataset(event) {
    const id = event.currentTarget.dataset.id
    const nextId = this.data.datasetPreviewId === id ? '' : id
    const previewRequest = {}
    this.previewRequest = previewRequest
    this.setData({ datasetPreviewId: nextId, datasetPreview: null, datasetPreviewError: '' })
    if (!nextId) return
    try {
      const result = await request('/api/datasets/' + encodeURIComponent(id) + '/preview?limit=5')
      if (this.previewRequest === previewRequest) this.setData({ datasetPreview: result })
    } catch (error) {
      if (this.previewRequest === previewRequest) this.setData({ datasetPreviewError: error.message })
    }
  },

  toggleIndustrialAdvanced() {
    this.setData({ industrialAdvanced: !this.data.industrialAdvanced })
  },

  checkLlm() {
    return this.execute(async () => {
      this.setData({ llmStatus: { label: '检测中…' } })
      // The status probe is synchronous and never occupies the training queue.
      const result = await post('/api/llm/status', { apiKey: this.data.llmKey }) || {}
      const label = result.sdk && result.key ? 'SDK 与密钥就绪'
        : result.sdk ? 'SDK 就绪，缺少密钥' : 'SDK 未安装'
      this.setData({ llmStatus: { sdk: result.sdk, key: result.key, message: result.message, label } })
    })
  },

  applyRunFilter() {
    const needle = (this.data.runQuery || '').trim().toLowerCase()
    const runs = (this.allRuns || []).filter(item =>
      !needle || (item.id + ' ' + item.caption).toLowerCase().indexOf(needle) >= 0)
    const ranked = runs.filter(item => item.value !== '—')
      .concat(runs.filter(item => item.value === '—'))
    this.setData({ runs, allRuns: this.allRuns, recentRuns: ranked.slice(0, 5),
      runChoices: runs.map(item => ({ value: item.id, label: item.id })) })
  },

  field(event) {
    const { group, key } = event.currentTarget.dataset
    const value = event.detail.value
    this.setData(group ? { ['cfg.' + group + '.' + key]: value } : { [key]: value })
    if (key === 'runQuery') { this.applyRunFilter(); return }
    if (['trialQuery', 'trialLow', 'trialHigh', 'logQuery'].includes(key)) {
      clearTimeout(this.searchTimer)
      this.searchTimer = setTimeout(() => {
        if (key === 'logQuery') this.loadEvents(1)
        else this.reloadTrials(1)
      }, 350)
    }
  },

  toggle(event) {
    this.setData({ [event.currentTarget.dataset.key]: event.detail.value })
  },

  pick(event) {
    const { key } = event.currentTarget.dataset
    const value = Number(event.detail.value)
    const patch = { [key]: value }
    if (key === 'method') patch.methodHint = C.methodHints[C.methods[value]] || ''
    if (key === 'dataset' || key === 'industrialDataset') {
      const next = key === 'dataset' ? C.regression[value] : C.industrial[value]
      if (this.data.upload && this.data.upload.key !== next) {
        patch.upload = null
        patch.csvInspection = null
      }
    }
    this.setData(patch)
  },

  pickText(event) {
    const { key, source } = event.currentTarget.dataset
    const choice = (this.data[source] || [])[Number(event.detail.value)] || {}
    this.setData({ [key]: choice.value === undefined ? choice : choice.value }, () => {
      if (key.startsWith('trial')) this.reloadTrials(1)
      if (key === 'chartKind' || key === 'chartParam') this.drawChart()
    })
  },

  paramChoices(params, blankLabel) {
    return [{ value: '', label: blankLabel }].concat(
      (params || []).map(name => ({ value: name, label: name })))
  },

  keyForTask(task) {
    if (task === 4) return 'cnc_milling_2025'
    if (task === 3) return C.industrial[this.data.industrialDataset]
    return C.regression[this.data.dataset]
  },

  currentDatasetKey() { return this.keyForTask(this.data.task) },

  clearUpload() { this.setData({ upload: null, csvInspection: null }) },

  uploadFiles() {
    return this.execute(async () => {
      const isUserCsv = this.currentDatasetKey() === 'user_csv'
      const picked = await new Promise((resolve, reject) => wx.chooseMessageFile({
        count: isUserCsv ? 1 : 9, type: 'file', success: resolve, fail: reject
      }))
      const key = this.currentDatasetKey()
      const existing = !isUserCsv && this.data.upload && this.data.upload.key === key ? this.data.upload : null
      let id = existing ? existing.id : ''
      const names = existing ? existing.names.slice() : []
      for (const file of picked.tempFiles) {
        const ext = file.name.split('.').pop().toLowerCase()
        if (isUserCsv && ext !== 'csv') throw new Error('自定义数据集只支持单个 CSV 文件')
        if (!['csv', 'zip', 'rar', 'mat', 'json', 'txt'].includes(ext)) {
          throw new Error('仅支持 csv / zip / rar / mat / json / txt：' + file.name)
        }
        if (file.size > 32 * 1024 * 1024) throw new Error('单文件超过 32 MB，请由管理员放入工作区：' + file.name)
        const data = await new Promise((resolve, reject) => wx.getFileSystemManager().readFile({
          filePath: file.path, success: res => resolve(res.data), fail: reject
        }))
        const path = '/api/uploads?' + buildQuery({ ext, name: file.name, upload: id || '' })
        const saved = await request(path, { method: 'POST', data, contentType: 'application/octet-stream' })
        id = saved.id
        names.push(saved.name)
      }
      // WXML bindings cannot call JS methods, so precompute the joined label here.
      this.setData({ upload: { id, key, names, label: names.join('、'), count: names.length }, csvInspection: null })
      if (isUserCsv) {
        const inspection = await request('/api/uploads/' + id + '/inspection')
        this.setData({ csvInspection: inspection, csvTarget: '', csvGroup: '', csvFeatures: '' })
      }
    })
  },

  checked(value, label, range) {
    const number = Number(value)
    if (!isFinite(number) || number < range[0] || number > range[1]) {
      throw new Error(label + ' 需在 ' + range[0] + '–' + range[1] + ' 之间')
    }
    return number
  },

  buildParams(action) {
    const d = this.data
    const isIndustrial = action.startsWith('industrial_')
    const params = {
      action,
      dataset: isIndustrial ? C.industrial[d.industrialDataset] : C.regression[d.dataset]
    }
    if (d.upload && d.upload.key === this.currentDatasetKey()) params.upload = d.upload.id
    if (isIndustrial && params.dataset === 'user_csv' && ['industrial_run', 'industrial_audit'].includes(action)) {
      if (!d.upload || !d.csvInspection) throw new Error('请先上传并检查 CSV 文件')
      const features = d.csvFeatures.split(/[,，]/).map(name => name.trim()).filter(Boolean)
      if (!d.csvTarget.trim() || !d.csvGroup.trim() || !features.length) throw new Error('请填写标签、分组和特征列名')
      params.mapping = { target: d.csvTarget.trim(), group: d.csvGroup.trim(), features }
    }
    if (action === 'train') {
      params.algorithm = 'baseline'
      params.seed = this.checked(d.cfg.train.seed, '随机种子', C.limits.seed)
      params.estimators = this.checked(d.cfg.train.estimators, '最大树数', C.limits.estimators)
    }
    if (action === 'optimize') {
      const c = d.cfg.optimize
      params.algorithm = C.methods[d.method]
      params.seed = this.checked(c.seed, '随机种子', C.limits.seed)
      params.estimators = this.checked(c.estimators, '最大树数', C.limits.estimators)
      params.trials = this.checked(c.trials, '试验次数', C.limits.trials)
      params.timeout = this.checked(c.timeout, '时间预算', C.limits.timeout)
    }
    if (action === 'industrial_run') {
      const c = d.cfg.industrial
      params.algorithm = C.industrialMethods[d.industrialMethod]
      params.model = C.models[d.model]
      params.decision = C.decisions[d.decision]
      params.seed = this.checked(c.seed, '随机种子', C.limits.seed)
      params.estimators = this.checked(c.estimators, '最大树数', C.limits.estimators)
      params.trials = this.checked(c.trials, '试验次数', C.limits.trials)
      params.timeout = this.checked(c.timeout, '时间预算', C.limits.timeout)
    }
    if (action === 'llm_train') {
      const c = d.cfg.llm
      // No key in the form is fine: the backend may already have one in .env.
      // Only block when a completed check explicitly reported "no key".
      if (!d.llmKey.trim() && d.llmStatus && d.llmStatus.sdk && d.llmStatus.key === false) {
        throw new Error('请先填写 PyTRIO API key（或设置环境变量 PYTRIO_API_KEY）')
      }
      params.baseModel = C.llmModels[d.llmModel || 0]
      if (d.llmKey.trim()) params.apiKey = d.llmKey.trim()
      params.examples = C.llmExamples
      params.iterations = this.checked(c.iterations, '训练轮数', [1, 100])
      params.learningRate = Number(c.learningRate)
      if (!(params.learningRate >= 0.000001 && params.learningRate <= 0.1)) {
        throw new Error('学习率需在 0.000001–0.1 之间')
      }
      params.rank = this.checked(c.rank, 'LoRA 秩', [4, 64])
      params.maxTokens = this.checked(c.maxTokens, '生成长度', [1, 512])
      params.evaluate = d.llmEvaluate
      params.name = 'lab-sft-' + Date.now().toString(36)
    }
    if (action === 'loop_create') {
      const c = d.cfg.loop
      params.authorize = d.authorize
      params.manual = d.manual
      params.policy = {
        scenario: C.scenarios[d.scenario],
        max_trials: this.checked(c.trials, '试验次数', C.limits.loopTrials),
        timeout_seconds: this.checked(c.timeout, '搜索时间', C.limits.loopTimeout),
        training_budget_seconds: this.checked(c.trainBudget, '训练总预算', C.limits.trainBudget)
      }
    }
    if (['finalize', 'promote', 'rollback', 'decline', 'loop_run'].includes(action)) {
      if (!d.detail) throw new Error('请先打开记录详情')
      params.id = d.viewingId || d.detail.id
      params.revision = d.detail.revision
      if (action === 'loop_run') params.manual = d.manual
    }
    return params
  },

  async submit(event) {
    if (this.data.submittingAction) return
    const { action, form } = event.currentTarget.dataset
    const feedbackPath = form === undefined ? '' : 'taskFeedback.form' + form
    this.setData({ submittingAction: action, error: '',
      ...(feedbackPath ? { [feedbackPath]: { status: 'submitting' } } : {}) })
    try {
      const params = this.buildParams(action)
      if (['finalize', 'promote', 'rollback', 'decline'].includes(action)) {
        const answer = await new Promise(resolve => wx.showModal({
          title: '确认操作',
          content: action === 'finalize' ? '将使用封存测试集进行一次最终评估。' : '将变更离线回放中的模型状态。',
          success: resolve, fail: () => resolve({})
        }))
        if (!answer.confirm) return
        params.confirmed = true
      }
      const job = await post('/api/jobs', params)
      const tracked = this.watchedJobs || (this.watchedJobs = {})
      tracked[job.id] = true
      this.setData({ active: { ...job, action }, freshId: '',
        ...(feedbackPath ? { [feedbackPath]: { id: job.id, status: 'submitted' } } : {}) })
      this.watch()
    } catch (error) {
      this.setData(feedbackPath ? { [feedbackPath]: { status: 'failed', error: error.message } }
        : { error: error.message })
    } finally {
      this.setData({ submittingAction: '' })
    }
  },

  updateTaskFeedback(job) {
    const key = Object.keys(this.data.taskFeedback).find(key => this.data.taskFeedback[key].id === job.id)
    if (!key) return false
    const status = job.status
    const resultId = ((job.result || {}).id) || (status === 'completed' ? job.id : '')
    const error = job.error || ''
    const previous = this.data.taskFeedback[key]
    if (previous.status !== status || previous.resultId !== resultId || previous.error !== error) {
      this.setData({ ['taskFeedback.' + key]: { id: job.id, status, resultId, error } })
    }
    return true
  },

  cancel() { return this.execute(() => post('/api/jobs/' + this.data.active.id + '/cancel', {})) },

  watch() {
    clearTimeout(this.timer)
    if (!this.visible || !this.data.role) return
    this.timer = setTimeout(async () => {
      try {
        const jobs = await request('/api/jobs')
        const running = jobs.filter(job => ['running', 'cancelling'].includes(job.status))
        const tracked = this.watchedJobs || (this.watchedJobs = {})
        const ids = Array.from(new Set(Object.keys(tracked).concat(running.map(job => job.id))))

        let active = this.data.active
        if (!active || !['running', 'cancelling'].includes(active.status)) {
          active = running.find(job => job.id === (active && active.id)) || running[0] || null
        }
        if (active) {
          const updated = await request('/api/jobs/' + active.id)
          const hasFormFeedback = this.updateTaskFeedback(updated)
          if (['running', 'cancelling'].includes(updated.status)) {
            tracked[updated.id] = true
            if (this.data.tab === 2 && this.data.logJobId === updated.id) this.loadEvents()
            if (this.data.active === active || !this.data.active ||
                !['running', 'cancelling'].includes(this.data.active.status)) {
              this.setData({ active: { id: updated.id, status: updated.status, action: updated.action,
                progress: updated.progress ? V.pretty(updated.progress, 1200) : '' } })
            }
          } else {
            delete tracked[updated.id]
            await this.finishJob(updated, hasFormFeedback)
          }
        }
        // A PyTRIO job can now run beside a local experiment: watch both to completion.
        for (const id of ids) {
          if (active && id === active.id) continue
          const updated = await request('/api/jobs/' + id)
          const hasFormFeedback = this.updateTaskFeedback(updated)
          if (['running', 'cancelling'].includes(updated.status)) { tracked[id] = true; continue }
          delete tracked[id]
          const resultId = (updated.result && updated.result.id) || ''
          if (resultId) {
            this.setData({ freshId: resultId })
            if (!hasFormFeedback && !this.data.viewingId) { await this.refresh(); await this.openDetail(resultId) }
          }
        }
      } catch (error) { this.setData({ error: error.message }) }
      if (this.visible) this.watch()
    }, 1500)
  },

  async finishJob(updated, hasFormFeedback) {
    const result = updated.result || {}
    const resultId = result.id || ''
    const patch = {
      active: { id: updated.id, status: updated.status, action: updated.action, progress: '' },
      activeResultId: resultId || this.data.activeResultId,
      freshId: resultId || this.data.freshId
    }
    if (!hasFormFeedback) patch.error = updated.error || ''
    if (!hasFormFeedback && !resultId && !this.data.viewingId) {
      patch.output = V.pretty(updated.events || result)
      patch.sections = V.dataSections(result)
      if (result.split_counts) patch.visual = V.visuals(null, null, result)
    }
    await this.refresh()
    if (!hasFormFeedback && resultId && !this.data.viewingId) await this.openDetail(resultId)
    if (this.data.active && this.data.active.id !== updated.id &&
        ['running', 'cancelling'].includes(this.data.active.status)) delete patch.active
    this.setData(patch)
  },

  resourceOf(id) {
    return id.startsWith('LOOP-') ? 'loops' : id.startsWith('JOB-') ? 'jobs' : 'runs'
  },

  sectionsFor(detail, resource) {
    if (resource === 'jobs') {
      return V.jobSections(detail).concat(V.dataSections(detail.result).filter(section => section.rows.length))
    }
    if (String(detail.id || '').startsWith('LLM-')) return V.llmSections(detail)
    if (resource === 'loops' || String(detail.id || '').startsWith('LOOP-')) return V.loopSections(detail)
    if (String(detail.id || '').startsWith('IND-')) return V.industrialSections(detail)
    if (detail.algorithm === 'baseline') return V.trainSections(detail)
    return V.optimizeSections(detail)
  },

  openDetail(id) {
    return this.execute(async () => {
      const resource = this.resourceOf(id)
      this.stopWave()
      this.waveView = null
      const detail = await request('/api/' + resource + '/' + id)
      const series = detail.charts || null
      const params = (series && series.parameters) || []
      const isLlmDetail = String(detail.id || '').startsWith('LLM-')
      const patch = {
        tab: 1, viewingId: id, viewingResource: resource, detail, isLlmDetail,
        output: V.pretty(detail), sections: this.sectionsFor(detail, resource),
        resultSummary: V.resultSummary(detail),
        chartMeta: series || null,
        chartSeries: null, chartParams: params,
        chartParam: params.indexOf(this.data.chartParam) >= 0 ? this.data.chartParam : (params[0] || ''),
        trialParamChoices: this.paramChoices(params, '不按参数范围筛选'),
        chartParamChoices: this.paramChoices(params, '选择参数'),
        logJobId: resource === 'jobs' ? id : this.data.logJobId,
        compare: null, compareBars: { rows: [], note: '' },
        visual: V.visuals(detail, series, resource === 'jobs' ? detail.result : null),
        wave: { index: 0, running: false, done: false, total: 0, current: '—' }
      }
      this.setData(patch, () => {
        if (resource === 'runs' && !isLlmDetail) this.reloadTrials(1)
        if (resource === 'jobs') this.loadEvents(1)
        if (isLlmDetail) this.drawLlmLoss()
        this.drawChart()
        this.drawVisuals()
        if (wx.pageScrollTo) wx.pageScrollTo({ scrollTop: 0, duration: 0 })
      })
    })
  },

  details(event) { return this.openDetail(event.currentTarget.dataset.id) },

  openJobLog(event) {
    this.setData({ logJobId: event.currentTarget.dataset.id }, () => this.loadEvents(1))
  },

  pickRun(event) {
    const choice = this.data.runChoices[Number(event.detail.value)]
    if (choice) return this.openDetail(choice.value)
  },

  returnToActive() {
    if (!this.data.activeResultId) {
      this.setData({ error: '当前任务还没有产生实验记录' })
      return
    }
    return this.openDetail(this.data.activeResultId)
  },

  openFresh() {
    if (this.data.freshId) return this.openDetail(this.data.freshId)
  },

  trialFilters() {
    const d = this.data
    return {
      q: d.trialQuery, status: d.trialStatus, sort: d.trialSort, order: d.trialOrder,
      param: d.trialParam, low: d.trialLow, high: d.trialHigh
    }
  },

  reloadTrials(page) {
    return this.execute(async () => {
      const id = this.data.viewingId
      if (!id || this.data.viewingResource !== 'runs') return
      const d = this.data
      const filters = this.trialFilters()
      const [table, series] = await Promise.all([
        request('/api/runs/' + id + '/trials?' + buildQuery({ ...filters,
          page: page || d.trials.page, size: d.trials.size })),
        request('/api/runs/' + id + '/charts?' + buildQuery(filters))
      ])
      const unit = (d.detail || {}).target_unit
      const visual = V.visuals(d.detail, series)
      this.setData({
        trials: { rows: V.trialRows(table.rows, unit, series.objective_label),
          total: table.total, page: table.page, size: table.size,
          pages: Math.max(1, Math.ceil(table.total / table.size)) },
        chartSeries: series, chartParams: series.parameters || [],
        chartParam: series.parameters && series.parameters.indexOf(d.chartParam) >= 0 ?
          d.chartParam : (series.parameters && series.parameters[0]) || '',
        visual,
        wave: { index: 0, running: false, done: false, total: visual.wave.points.length, current: '—' }
      }, () => { this.drawChart(); this.drawVisuals(); this.waveStart() })
    })
  },

  trialPage(event) {
    const delta = Number(event.currentTarget.dataset.delta)
    const page = Math.min(this.data.trials.pages, Math.max(1, this.data.trials.page + delta))
    if (page !== this.data.trials.page) this.reloadTrials(page)
  },

  trialCheck(event) {
    const checked = Object.assign({}, this.data.trialChecked)
    for (const row of this.data.trials.rows) {
      const key = String(row.number)
      if (event.detail.value.indexOf(key) >= 0) checked[key] = row
      else delete checked[key]
    }
    this.setData({ trialChecked: checked, trialCheckedCount: Object.keys(checked).length })
  },

  trialDetail(event) {
    const number = String(event.currentTarget.dataset.number)
    const row = this.data.trials.rows.find(item => String(item.number) === number)
    if (row) this.setData({ output: V.pretty(row) })
  },

  compareTrials() {
    const rows = Object.keys(this.data.trialChecked).map(key => this.data.trialChecked[key])
    if (rows.length < 2 || rows.length > 8) {
      this.setData({ error: '请勾选 2 至 8 条试验再比较' })
      return
    }
    const fields = [['试验', 'number'], ['状态', 'status'], ['目标', 'objective'],
      ['验证 MAE / 分类错误', 'mae'], ['耗时（秒）', 'duration'], ['参数', 'params']]
    this.setData({ compare: {
      columns: fields.map(([label, key]) => ({ label, values: rows.map(item => V.text(item[key])) })),
      ids: rows.map(item => '#' + item.number),
      note: '同一实验内的试验并排查看；跨实验只并排展示，不生成排名。',
      ranking: false
    } })
  },

  selectRun(event) {
    const selected = Object.assign({}, this.data.selected)
    for (const item of this.data.runs) delete selected[item.id]
    for (const id of event.detail.value) selected[id] = true
    this.setData({ selected, selectedCount: Object.keys(selected).length })
  },

  compareRuns() {
    return this.execute(async () => {
      const ids = Object.keys(this.data.selected)
      if (ids.length < 2 || ids.length > 8) throw new Error('请勾选 2 至 8 条实验再比较')
      const query = ids.map(id => 'id=' + encodeURIComponent(id)).join('&')
      const result = await request('/api/compare?' + query)
      this.setData({ tab: 1, compare: V.compareView(result), compareBars: V.compareBars(result) }, () => {
        wx.pageScrollTo({ selector: '#experiment-comparison', duration: 200, fail() {} })
      })
    })
  },

  toggleCompareRun(event) {
    const id = event.currentTarget.dataset.id
    const selected = Object.assign({}, this.data.selected)
    if (selected[id]) delete selected[id]
    else selected[id] = true
    this.setData({ selected, selectedCount: Object.keys(selected).length })
  },

  clearCompareSelection() {
    this.setData({ selected: {}, selectedCount: 0 })
  },

  setChartKind(event) {
    this.setData({ chartKind: event.currentTarget.dataset.value }, () => this.drawChart())
  },

  gotoRecords() { this.setData({ tab: 2 }, () => this.refresh()) },

  gotoWorkspace(event) {
    const id = event.currentTarget.dataset.id
    this.setData({ tab: 1 }, () => {
      this.refresh()
      if (id) this.openDetail(id)
    })
  },

  loadEvents(page) {
    return this.execute(async () => {
      const id = this.data.logJobId || (this.data.active && this.data.active.id)
      if (!id) return
      const data = await request('/api/jobs/' + id + '/events?' + buildQuery({
        q: this.data.logQuery, page: page || this.data.logPage, size: this.data.logSize
      }))
      const rows = V.logRows(data.rows)
      this.setData({
        logJobId: id, logRows: rows, logTotal: data.total, logPage: data.page,
        logTarget: this.data.autoScroll && rows.length ? 'log-' + (rows.length - 1) : ''
      })
    })
  },

  logPage(event) {
    const delta = Number(event.currentTarget.dataset.delta)
    const pages = Math.max(1, Math.ceil(this.data.logTotal / this.data.logSize))
    const page = Math.min(pages, Math.max(1, this.data.logPage + delta))
    if (page !== this.data.logPage) this.loadEvents(page)
  },

  clearOutput() {
    this.stopWave()
    this.setData({ output: '', sections: [], logRows: [], compare: null,
      visual: V.visuals(null, null, null), compareBars: { rows: [], note: '' },
      wave: { index: 0, running: false, done: false, total: 0, current: '—' } })
  },
  copyOutput() { wx.setClipboardData({ data: this.data.output }) },

  exportLog() {
    return this.execute(async () => {
      const id = this.data.logJobId
      if (!id) throw new Error('请先打开任务日志')
      const filePath = wx.env.USER_DATA_PATH + '/' + id + '-events.jsonl'
      const file = await new Promise((resolve, reject) => wx.downloadFile({
        url: base + '/api/jobs/' + id + '/export', header: localHeaders, filePath,
        success: res => res.statusCode === 200 ? resolve(res.filePath) : reject(new Error('导出失败 ' + res.statusCode)),
        fail: reject
      }))
      if (wx.shareFileMessage) wx.shareFileMessage({ filePath: file, fileName: id + '-events.jsonl' })
      else this.setData({ output: '已保存：' + file })
    })
  },

  exportRun() {
    return this.execute(async () => {
      const id = this.data.viewingId
      if (!id) throw new Error('请先打开实验详情')
      const resource = this.data.viewingResource
      const filePath = wx.env.USER_DATA_PATH + '/' + id + '.zip'
      const file = await new Promise((resolve, reject) => wx.downloadFile({
        url: base + '/api/' + resource + '/' + id + '/export', header: localHeaders, filePath,
        success: res => res.statusCode === 200 ? resolve(res.filePath) : reject(new Error('导出失败 ' + res.statusCode)),
        fail: reject
      }))
      if (wx.shareFileMessage) wx.shareFileMessage({ filePath: file, fileName: id + '.zip' })
      else this.setData({ output: '已保存：' + file })
    })
  },

  drawChart() {
    if (this.data.tab !== 1 || !this.data.chartSeries) return
    Charts.draw(this, 'chart', this.data.chartKind, this.data.chartSeries, this.data.chartParam)
      .catch(error => this.setData({ error: error.message }))
  },

  drawLlmLoss() {
    if (this.data.tab !== 1 || !this.data.isLlmDetail) return
    const losses = (this.data.detail && this.data.detail.losses) || []
    if (!losses.length) return
    Charts.drawLoss(this, 'llm-loss', losses)
      .catch(error => this.setData({ error: error.message }))
  },

  drawVisuals() {
    const visual = this.data.visual
    if (this.data.tab !== 1 || !this.visible || !visual || !visual.any) return
    const jobs = []
    if (visual.ring.segments.length) jobs.push(Charts.drawRing(this, 'ring', visual.ring.segments))
    if (visual.curve.points.length) jobs.push(Charts.drawCurve(this, 'curve', visual.curve.points))
    Promise.all(jobs).catch(error => this.setData({ error: error.message }))
  },

  async paintWave() {
    if (this.data.tab !== 1) return
    const wave = this.data.visual.wave
    if (!wave || !wave.points.length) return
    try {
      if (!this.waveView) this.waveView = await Charts.context(this, 'wave')
      if (!this.visible || this.data.tab !== 1) return
      Charts.paintWave(this.waveView, wave, this.data.wave.index)
    } catch (error) {
      this.waveView = null
    }
  },

  waveStart() {
    const wave = this.data.visual.wave
    if (!wave || !wave.points.length || this.data.tab !== 1) return
    this.stopWave()
    const index = this.data.wave.done ? 0 : this.data.wave.index
    this.setData({ wave: { index, running: true, done: false,
      total: wave.points.length, current: this.waveCurrent(index) } })
    this.paintWave()
    this.waveTimer = setInterval(() => this.waveTick(), 120)
  },

  waveTick() {
    const wave = this.data.visual.wave
    if (!wave || !wave.points.length || !this.visible || this.data.tab !== 1) {
      this.stopWave()
      return
    }
    const step = Math.max(1, Math.ceil(wave.points.length / 80))
    const index = Math.min(wave.points.length, this.data.wave.index + step)
    const done = index >= wave.points.length
    this.setData({ wave: { index, running: !done, done,
      total: wave.points.length, current: this.waveCurrent(index) } })
    this.paintWave()
    if (done) this.stopWave()
  },

  waveCurrent(index) {
    const wave = this.data.visual.wave
    if (!wave || !wave.points.length) return '—'
    const position = Math.max(1, Math.min(index || 1, wave.points.length))
    const point = wave.points[position - 1]
    if (!point) return '—'
    const value = point.values.find(item => typeof item === 'number' && isFinite(item))
    return value === undefined ? '—' : V.text(value)
  },

  waveToggle() {
    if (this.data.wave.running) this.stopWave()
    else this.waveStart()
  },

  waveReplay() {
    this.stopWave()
    this.setData({ wave: { index: 0, running: false, done: false,
      total: (this.data.visual.wave.points || []).length, current: '—' } },
    () => this.waveStart())
  },

  stopWave() {
    if (this.waveTimer) { clearInterval(this.waveTimer); this.waveTimer = null }
    if (this.data.wave && this.data.wave.running) {
      this.setData({ wave: Object.assign({}, this.data.wave, { running: false }) })
    }
  }
})
