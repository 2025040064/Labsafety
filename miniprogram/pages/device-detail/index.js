const { failureMessage, statusView, unwrap } = require('../_shared/ui')

function demoRequestId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

Page({
  data: {
    deviceId: '',
    loading: true,
    submitting: false,
    error: '',
    device: null,
    title: '',
    description: '',
    severityChoices: [
      { value: 'LOW', label: '低' },
      { value: 'MEDIUM', label: '中' },
      { value: 'HIGH', label: '高' }
    ],
    severityIndex: 0,
    canManage: false,
    editForm: null
  },

  onLoad(options) {
    const deviceId = options.id ? decodeURIComponent(options.id) : ''
    this.setData({ deviceId })
  },

  onShow() {
    this.load()
  },

  async load() {
    if (!this.data.deviceId) {
      this.setData({ loading: false, error: '缺少设备编号' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      const core = await getApp().corePromise
      const [value, session] = await Promise.all([
        core.query({ type: 'device.detail', payload: { deviceId: this.data.deviceId } }),
        core.query({ type: 'demo.session' })
      ])
      const device = value.device
      if (!device) {
        this.setData({ device: null, error: '设备不存在或演示数据已恢复，请返回设备台账。' })
        return
      }
      this.setData({
        // 编辑与停用/启用是管理员的演示动作；角色切换只是本机演示规则，不是真实权限。
        canManage: session.role === 'admin',
        device: {
          ...device,
          codeText: device.code || device.id,
          labText: device.labName || '未指定实验室',
          modelText: device.model || '未填写',
          isActive: device.status === 'ACTIVE',
          ...statusView(device.status)
        }
      })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  onTitleInput(event) {
    this.setData({ title: event.detail.value })
  },

  onDescriptionInput(event) {
    this.setData({ description: event.detail.value })
  },

  onSeverityChange(event) {
    this.setData({ severityIndex: Number(event.detail.value) })
  },

  onEditInput(event) {
    const field = event.currentTarget.dataset.field
    if (!field) return
    this.setData({ ['editForm.' + field]: event.detail.value })
  },

  startEdit() {
    const device = this.data.device
    if (!this.data.canManage) {
      this.setData({ error: '设备编辑仅限演示管理员；角色切换只是本机演示规则，不是真实权限。' })
      return
    }
    if (!device || !device.isActive) return
    this.setData({ editForm: { code: device.code || '', name: device.name || '', model: device.model || '' }, error: '' })
  },

  cancelEdit() {
    this.setData({ editForm: null })
  },

  async saveEdit() {
    const device = this.data.device
    const form = this.data.editForm
    if (!device || !form || this.data.submitting) return
    const code = form.code.trim()
    const name = form.name.trim()
    const model = form.model.trim()
    if (code.length < 2) {
      this.setData({ error: '设备编号需为 2 至 20 位字母、数字或连字符' })
      return
    }
    if (name.length < 2) {
      this.setData({ error: '设备名称需为 2 至 40 字' })
      return
    }
    const payload = { deviceId: device.id, code, name, model }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingCommand || this.pendingCommand.fingerprint !== fingerprint) {
      this.pendingCommand = { fingerprint, requestId: demoRequestId('dev-edit') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type: 'device.update', requestId: this.pendingCommand.requestId, payload }))
      this.pendingCommand = null
      this.setData({ editForm: null })
      await this.load()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  },

  async toggleActive() {
    const device = this.data.device
    if (!device || this.data.submitting) return
    if (!this.data.canManage) {
      this.setData({ error: '设备停用/启用仅限演示管理员；角色切换只是本机演示规则，不是真实权限。' })
      return
    }
    const type = device.isActive ? 'device.disable' : 'device.enable'
    if (!this.pendingCommand || this.pendingCommand.type !== type) {
      this.pendingCommand = { type, requestId: demoRequestId(device.isActive ? 'dev-disable' : 'dev-enable') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      const data = unwrap(await core.execute({ type, requestId: this.pendingCommand.requestId, payload: { deviceId: device.id } }))
      this.pendingCommand = null
      this.setData({ editForm: null })
      await this.load()
      if (type === 'device.disable' && data.cancelledInspectionCount > 0) {
        wx.showToast({ title: `已停用，并取消 ${data.cancelledInspectionCount} 项待巡检`, icon: 'none' })
      }
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  },

  async reportIncident() {
    if (this.data.submitting || !this.data.device || !this.data.device.isActive) return
    const payload = {
      deviceId: this.data.device.id,
      title: this.data.title.trim(),
      description: this.data.description.trim(),
      severity: this.data.severityChoices[this.data.severityIndex].value
    }
    if (payload.title.length < 2) {
      this.setData({ error: '请填写至少 2 个字的异常标题' })
      return
    }
    if (!payload.description) {
      this.setData({ error: '请描述看到的模拟异常现象' })
      return
    }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingSubmission || this.pendingSubmission.fingerprint !== fingerprint) {
      this.pendingSubmission = {
        fingerprint,
        requestId: 'report-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
      }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      const data = unwrap(await core.execute({
        type: 'incident.report',
        requestId: this.pendingSubmission.requestId,
        payload
      }))
      this.pendingSubmission = null
      if (data.incident && data.incident.id) {
        wx.redirectTo({ url: '/pages/incident-detail/index?id=' + encodeURIComponent(data.incident.id) })
      } else {
        wx.switchTab({ url: '/pages/workbench/index' })
      }
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
