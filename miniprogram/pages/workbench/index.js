const { failureMessage, shortDate, statusView, unwrap, SEVERITY_LABELS } = require('../_shared/ui')

function demoRequestId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

/** 与核心列表过滤契约一致：空值表示不过滤，其余按精确状态匹配。 */
const INSPECTION_FILTERS = [
  { value: '', label: '全部' },
  { value: 'PLANNED', label: '待巡检' },
  { value: 'SUBMITTED', label: '待复核' },
  { value: 'CANCELLED', label: '已取消' }
]

const INCIDENT_FILTERS = [
  { value: '', label: '全部' },
  { value: 'REPORTED', label: '已上报' },
  { value: 'CONFIRMED', label: '已确认' },
  { value: 'ASSIGNED', label: '已分派' },
  { value: 'IN_PROGRESS', label: '处理中' },
  { value: 'PENDING_REVIEW', label: '待复核' },
  { value: 'CLOSED', label: '已关闭' },
  { value: 'REJECTED', label: '已驳回' }
]

Page({
  data: {
    loading: true,
    error: '',
    activeSection: 'inspection',
    inspections: [],
    incidents: [],
    inspectionFilters: INSPECTION_FILTERS,
    incidentFilters: INCIDENT_FILTERS,
    inspectionFilter: '',
    incidentFilter: '',
    templates: [],
    activeDevices: [],
    formOpen: false,
    formTemplateIndex: 0,
    formDeviceIndex: 0,
    formItems: [],
    submitting: false
  },

  onShow() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const core = await getApp().corePromise
      const [inspectionResult, incidentResult, templateResult, deviceResult] = await Promise.all([
        core.query({ type: 'inspection.list' }),
        core.query({ type: 'incident.list' }),
        core.query({ type: 'inspection.template.list' }),
        core.query({ type: 'device.list' })
      ])
      this.allInspections = (unwrap(inspectionResult).items || []).map((task) => ({
        ...task,
        dueText: shortDate(task.dueAt),
        deviceText: task.deviceName || '未关联设备',
        ...statusView(task.status)
      }))
      this.allIncidents = (unwrap(incidentResult).items || []).map((incident) => ({
        ...incident,
        codeText: incident.code || incident.id,
        deviceText: incident.deviceName || '未关联设备',
        severityText: SEVERITY_LABELS[String(incident.severity || '').toUpperCase()] || incident.severity || '未分级',
        ...statusView(incident.status)
      }))
      this.setData({
        templates: unwrap(templateResult).items || [],
        activeDevices: (unwrap(deviceResult).items || []).filter((device) => device.status === 'ACTIVE')
      })
      this.applyLists()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  applyLists() {
    const inspectionFilter = this.data.inspectionFilter
    const incidentFilter = this.data.incidentFilter
    this.setData({
      inspections: this.allInspections.filter((task) => !inspectionFilter || task.status === inspectionFilter),
      incidents: this.allIncidents.filter((incident) => !incidentFilter || incident.status === incidentFilter)
    })
  },

  selectInspectionFilter(event) {
    this.setData({ inspectionFilter: event.currentTarget.dataset.value || '' })
    this.applyLists()
  },

  selectIncidentFilter(event) {
    this.setData({ incidentFilter: event.currentTarget.dataset.value || '' })
    this.applyLists()
  },

  selectSection(event) {
    this.setData({ activeSection: event.currentTarget.dataset.section })
  },

  openIncident(event) {
    const id = event.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/incident-detail/index?id=' + encodeURIComponent(id) })
  },

  toggleForm() {
    if (this.data.formOpen) {
      this.setData({ formOpen: false, error: '' })
      return
    }
    if (this.data.templates.length === 0 || this.data.activeDevices.length === 0) {
      this.setData({ error: '暂无可用的巡检模板或在用设备，可先在设备页登记，或恢复首页演示数据。' })
      return
    }
    this.setData({
      formOpen: true,
      error: '',
      formTemplateIndex: 0,
      formDeviceIndex: 0,
      formItems: this.buildFormItems(this.data.templates[0])
    })
  },

  buildFormItems(template) {
    return (template && Array.isArray(template.items) ? template.items : []).map((item) => ({
      text: item,
      passed: true,
      note: ''
    }))
  },

  pickTemplate(event) {
    const index = Number(event.detail.value)
    this.setData({
      formTemplateIndex: index,
      formItems: this.buildFormItems(this.data.templates[index])
    })
  },

  pickDevice(event) {
    this.setData({ formDeviceIndex: Number(event.detail.value) })
  },

  onItemPassedChange(event) {
    const index = event.currentTarget.dataset.index
    if (!Number.isInteger(index)) return
    this.setData({ ['formItems[' + index + '].passed']: !!event.detail.value })
  },

  onItemNoteInput(event) {
    const index = event.currentTarget.dataset.index
    if (!Number.isInteger(index)) return
    this.setData({ ['formItems[' + index + '].note']: event.detail.value })
  },

  async submitInspectionForm() {
    const template = this.data.templates[this.data.formTemplateIndex]
    const device = this.data.activeDevices[this.data.formDeviceIndex]
    if (!template || !device || this.data.submitting) return
    const payload = {
      templateId: template.id,
      deviceId: device.id,
      results: this.data.formItems.map((item) => ({ item: item.text, passed: !!item.passed, note: item.note.trim() }))
    }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingSubmission || this.pendingSubmission.fingerprint !== fingerprint) {
      this.pendingSubmission = { fingerprint, requestId: demoRequestId('insp') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type: 'inspection.submit', requestId: this.pendingSubmission.requestId, payload }))
      this.pendingSubmission = null
      this.setData({ formOpen: false })
      wx.showToast({ title: '巡检结果已提交', icon: 'success' })
      await this.load()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
