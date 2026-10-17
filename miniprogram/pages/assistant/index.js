const { failureMessage, unwrap, SEVERITY_LABELS } = require('../_shared/ui')

function demoRequestId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

const SEVERITY_CHOICES = [
  { value: 'LOW', label: '低' },
  { value: 'MEDIUM', label: '中' },
  { value: 'HIGH', label: '高' }
]

Page({
  data: {
    text: '',
    parsing: false,
    parsed: null,
    submitting: false,
    error: '',
    // 可编辑草稿（与解析结果分离，只有确认提交才写入）
    intent: '',
    devices: [],
    templates: [],
    deviceIndex: -1,
    templateIndex: -1,
    severityChoices: SEVERITY_CHOICES,
    severityIndex: 0,
    title: '',
    description: '',
    items: [],
    missing: []
  },

  onShow() {
    this.loadChoices()
  },

  async loadChoices() {
    try {
      const core = await getApp().corePromise
      const [deviceValue, templateValue] = await Promise.all([
        core.query({ type: 'device.list' }),
        core.query({ type: 'inspection.template.list' })
      ])
      const devices = (unwrap(deviceValue).items || []).map((device) => ({
        ...device,
        labelText: `${device.name}（${device.code}）${device.status === 'ACTIVE' ? '' : ' · 已停用'}`
      }))
      this.setData({ devices, templates: unwrap(templateValue).items || [] })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    }
  },

  onTextInput(event) {
    this.setData({ text: event.detail.value })
  },

  /** 解析只生成草稿，不写任何数据；同一输入可重复解析出一致结果。 */
  async parseDraft() {
    const text = this.data.text.trim()
    if (!text || this.data.parsing) return
    this.setData({ parsing: true, error: '' })
    try {
      const core = await getApp().corePromise
      const value = unwrap(await core.query({ type: 'assistant.parseDraft', payload: { text } }))
      this.applyDraft(value)
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ parsing: false })
    }
  },

  applyDraft(value) {
    const patch = {
      parsed: value,
      intent: value.intent,
      missing: value.missing || [],
      deviceIndex: -1,
      templateIndex: -1,
      title: '',
      description: '',
      severityIndex: 0,
      items: []
    }
    const draft = value.draft
    if (draft) {
      if (draft.deviceId) {
        const index = this.data.devices.findIndex((device) => device.id === draft.deviceId)
        patch.deviceIndex = index
      }
      if (draft.templateId) {
        patch.templateIndex = this.data.templates.findIndex((template) => template.id === draft.templateId)
      }
      if (value.intent === 'INCIDENT') {
        patch.title = draft.title || ''
        patch.description = draft.description || ''
        patch.severityIndex = Math.max(0, SEVERITY_CHOICES.findIndex((item) => item.value === draft.severity))
      }
      if (value.intent === 'INSPECTION') {
        patch.items = (draft.results || []).map((item) => ({ ...item }))
      }
    }
    this.setData(patch)
  },

  onDraftDeviceChange(event) {
    this.setData({ deviceIndex: Number(event.detail.value) })
  },

  onDraftTemplateChange(event) {
    this.setData({ templateIndex: Number(event.detail.value) })
  },

  onDraftTitleInput(event) {
    this.setData({ title: event.detail.value })
  },

  onDraftDescriptionInput(event) {
    this.setData({ description: event.detail.value })
  },

  onDraftSeverityChange(event) {
    this.setData({ severityIndex: Number(event.detail.value) })
  },

  onDraftItemPassedChange(event) {
    const index = event.currentTarget.dataset.index
    if (!Number.isInteger(index)) return
    this.setData({ ['items[' + index + '].passed']: !!event.detail.value })
  },

  onDraftItemNoteInput(event) {
    const index = event.currentTarget.dataset.index
    if (!Number.isInteger(index)) return
    this.setData({ ['items[' + index + '].note']: event.detail.value })
  },

  clearDraft() {
    this.setData({ parsed: null, intent: '', missing: [], items: [], deviceIndex: -1, templateIndex: -1, title: '', description: '', severityIndex: 0, error: '' })
  },

  /**
   * 缺字段时只提示、不提交；确认后才调用既有业务命令，
   * 设备无效或已停用等情况仍由核心拒绝，页面不绕过校验。
   */
  async submitDraft() {
    const parsed = this.data.parsed
    if (!parsed || this.data.submitting) return
    const device = this.data.devices[this.data.deviceIndex]
    if (parsed.intent === 'INCIDENT') {
      const missing = []
      if (!device) missing.push('设备')
      const title = this.data.title.trim()
      if (title.length < 2) missing.push('异常标题（至少 2 字）')
      if (missing.length) {
        this.setData({ error: '草稿还未提交：请补全 ' + missing.join('、') })
        return
      }
      await this.runSubmit('incident.report', {
        deviceId: device.id,
        title,
        description: this.data.description.trim(),
        severity: SEVERITY_CHOICES[this.data.severityIndex].value
      }, (data) => {
        if (data.incident && data.incident.id) {
          wx.redirectTo({ url: '/pages/incident-detail/index?id=' + encodeURIComponent(data.incident.id) })
        }
      })
      return
    }
    if (parsed.intent === 'INSPECTION') {
      const template = this.data.templates[this.data.templateIndex]
      const missing = []
      if (!device) missing.push('设备')
      if (!template) missing.push('巡检模板')
      if (missing.length) {
        this.setData({ error: '草稿还未提交：请补全 ' + missing.join('、') })
        return
      }
      await this.runSubmit('inspection.submit', {
        templateId: template.id,
        deviceId: device.id,
        results: this.data.items.map((item) => ({ item: item.item, passed: !!item.passed, note: (item.note || '').trim() }))
      }, () => {
        wx.showToast({ title: '巡检结果已提交', icon: 'success' })
        this.clearDraft()
      })
    }
  },

  async runSubmit(type, payload, onSuccess) {
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingSubmission || this.pendingSubmission.fingerprint !== fingerprint) {
      this.pendingSubmission = { fingerprint, requestId: demoRequestId('draft') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      const data = unwrap(await core.execute({ type, requestId: this.pendingSubmission.requestId, payload }))
      this.pendingSubmission = null
      if (onSuccess) onSuccess(data)
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
