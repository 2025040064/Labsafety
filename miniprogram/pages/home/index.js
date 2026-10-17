const { countText, failureMessage, unwrap } = require('../_shared/ui')

function sizeText(bytes) {
  return Number.isFinite(bytes) && bytes >= 0 ? `${Math.max(1, Math.ceil(bytes / 1024))} KB` : '未知'
}

Page({
  data: {
    loading: true,
    busy: false,
    error: '',
    role: '',
    roleLabel: '演示身份',
    roles: [
      { value: 'student', label: '学生' },
      { value: 'admin', label: '管理员' },
      { value: 'teacher', label: '教师' }
    ],
    stats: [
      { label: '设备总数', value: '—' },
      { label: '巡检任务', value: '—' },
      { label: '异常记录', value: '—' },
      { label: '待关闭异常', value: '—' }
    ],
    // 备份与恢复（G4）：导出只含 JSON 元数据，不含图片二进制
    backupText: '',
    backupMeta: null,
    copyMessage: '',
    copyFailed: false,
    importText: '',
    importReport: null,
    exporting: false
  },

  onShow() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const core = await getApp().corePromise
      const [summaryResult, sessionResult] = await Promise.all([
        core.query({ type: 'dashboard.summary' }),
        core.query({ type: 'demo.session' })
      ])
      const summary = unwrap(summaryResult)
      const session = unwrap(sessionResult)
      this.setData({
        role: session.role || '',
        roleLabel: session.label || '演示身份',
        stats: [
          { label: '设备总数', value: countText(summary.deviceCount) },
          { label: '巡检任务', value: countText(summary.inspectionCount) },
          { label: '异常记录', value: countText(summary.incidentCount) },
          { label: '待关闭异常', value: countText(summary.openIncidentCount) }
        ]
      })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  async switchRole(event) {
    const role = event.currentTarget.dataset.role
    if (this.data.busy || role === this.data.role) return
    this.setData({ busy: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type: 'demo.role.switch', payload: { role } }))
      await this.load()
      wx.showToast({ title: '演示身份已切换', icon: 'none' })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ busy: false })
    }
  },

  /** 导出 JSON 元数据备份并复制到剪贴板；容量告警只提示，不阻断。 */
  async exportBackup() {
    if (this.data.exporting) return
    this.setData({ exporting: true, error: '', backupText: '', backupMeta: null, copyMessage: '', copyFailed: false })
    try {
      const core = await getApp().corePromise
      const data = unwrap(await core.maintenance({ type: 'backup.export' }))
      const text = JSON.stringify(data.backup)
      this.setData({
        backupText: text,
        backupMeta: { byteSize: data.byteSize, sizeText: sizeText(data.byteSize), warnings: data.warnings || [] },
        copyMessage: '正在复制到剪贴板…',
        copyFailed: false
      })
      try {
        await new Promise((resolve, reject) => {
          wx.setClipboardData({ data: text, success: resolve, fail: reject })
        })
        this.setData({ copyMessage: '备份 JSON 已复制到剪贴板。' })
      } catch (error) {
        this.setData({
          copyMessage: '复制失败，请从下方文本框手动复制备份 JSON，或再次导出重试。',
          copyFailed: true
        })
      }
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ exporting: false })
    }
  },

  onImportInput(event) {
    this.setData({ importText: event.detail.value, importReport: null })
  },

  /** 预检只读不写：解析 JSON、校验结构/校验值，并给出迁移与附件缺失提示。 */
  async precheckImport() {
    const text = (this.data.importText || '').trim()
    if (!text) {
      this.setData({ error: '请先粘贴备份 JSON 后再预检' })
      return
    }
    let backup
    try { backup = JSON.parse(text) } catch (error) { 
      this.setData({ error: '备份内容不是有效 JSON：' + error.message, importReport: null })
      return
    }
    this.setData({ busy: true, error: '' })
    try {
      const core = await getApp().corePromise
      const data = unwrap(await core.maintenance({ type: 'backup.precheck', payload: { backup } }))
      this.setData({
        importReport: { ...data, sizeText: sizeText(data.byteSize) },
        error: ''
      })
    } catch (error) {
      this.setData({ error: failureMessage(error), importReport: null })
    } finally {
      this.setData({ busy: false })
    }
  },

  confirmRestore() {
    const report = this.data.importReport
    if (!report || !report.canRestore || this.data.busy) return
    const summaryParts = [`约 ${report.sizeText}`, report.migrated ? '将自动迁移旧版本数据' : '版本兼容，无需迁移']
    const missingAttachments = (report.counts && report.counts.attachments) > 0
      ? `备份含 ${report.counts.attachments} 条附件记录，异机恢复后图片文件会缺失。`
      : ''
    wx.showModal({
      title: '确认恢复备份',
      content: `将用备份覆盖当前本机演示数据（${summaryParts.join('；')}）。${missingAttachments}确定恢复？`,
      confirmText: '恢复',
      success: async (result) => {
        if (!result.confirm) return
        this.setData({ busy: true, error: '' })
        try {
          const core = await getApp().corePromise
          const backup = JSON.parse(this.data.importText)
          unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup } }))
          this.setData({ importText: '', importReport: null, backupText: '', backupMeta: null, copyMessage: '', copyFailed: false })
          await this.load()
          wx.showToast({ title: '备份已恢复', icon: 'success' })
        } catch (error) {
          this.setData({ error: failureMessage(error) })
        } finally {
          this.setData({ busy: false })
        }
      }
    })
  },

  resetDemo() {
    if (this.data.busy) return
    wx.showModal({
      title: '恢复模拟数据',
      content: '将恢复固定模拟数据，本机新增的演示记录会被替换。确定重置？',
      confirmText: '恢复数据',
      success: async (result) => {
        if (!result.confirm) return
        this.setData({ busy: true, error: '' })
        try {
          const core = await getApp().corePromise
          unwrap(await core.maintenance({ type: 'demo.reset' }))
          await this.load()
          wx.showToast({ title: '模拟数据已恢复', icon: 'none' })
        } catch (error) {
          this.setData({ error: failureMessage(error) })
        } finally {
          this.setData({ busy: false })
        }
      }
    })
  }
})
