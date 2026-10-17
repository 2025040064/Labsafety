const { failureMessage, statusView, unwrap } = require('../_shared/ui')

function demoRequestId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

Page({
  data: {
    loading: true,
    error: '',
    role: '',
    canManage: false,
    devices: [],
    labs: [],
    deviceLabOptions: [],
    managing: false,
    labForm: null,
    deviceForm: null,
    submitting: false
  },

  onShow() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const core = await getApp().corePromise
      const [deviceValue, labValue, session] = await Promise.all([
        core.query({ type: 'device.list' }),
        core.query({ type: 'lab.list' }),
        core.query({ type: 'demo.session' })
      ])
      // 台账编辑是管理员的演示动作；角色切换只是本机演示规则，不是真实权限。
      const canManage = session.role === 'admin'
      const labs = (unwrap(labValue).items || []).map((lab) => ({ ...lab }))
      const devices = (unwrap(deviceValue).items || []).map((device) => ({
        ...device,
        codeText: device.code || device.id,
        labText: device.labName || '未指定实验室',
        ...statusView(device.status)
      }))
      this.setData({
        role: session.role || 'student',
        canManage,
        labs,
        devices,
        deviceLabOptions: labs.filter((lab) => lab.status === 'ACTIVE').map((lab) => `${lab.name}（${lab.code}）`),
        managing: canManage ? this.data.managing : false
      })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  toggleManaging() {
    if (!this.data.canManage) {
      this.setData({ error: '台账编辑仅限演示管理员；角色切换只是本机演示规则，不是真实权限。' })
      return
    }
    this.setData({ managing: !this.data.managing, labForm: null, deviceForm: null, error: '' })
  },

  startCreateLab() {
    this.setData({ labForm: { labId: '', code: '', name: '', location: '' }, deviceForm: null, error: '' })
  },

  startEditLab(event) {
    const lab = this.data.labs.find((item) => item.id === event.currentTarget.dataset.id)
    if (!lab) return
    this.setData({
      labForm: { labId: lab.id, code: lab.code || '', name: lab.name || '', location: lab.location || '' },
      deviceForm: null,
      error: ''
    })
  },

  onLabFormInput(event) {
    const field = event.currentTarget.dataset.field
    if (!field) return
    this.setData({ ['labForm.' + field]: event.detail.value })
  },

  cancelLabForm() {
    this.setData({ labForm: null })
  },

  async saveLabForm() {
    const form = this.data.labForm
    if (!form || this.data.submitting) return
    const code = form.code.trim()
    const name = form.name.trim()
    const location = form.location.trim()
    if (code.length < 2) {
      this.setData({ error: '实验室编号需为 2 至 20 位字母、数字或连字符' })
      return
    }
    if (name.length < 2) {
      this.setData({ error: '实验室名称需为 2 至 40 字' })
      return
    }
    const payload = form.labId ? { labId: form.labId, code, name, location } : { code, name, location }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingCommand || this.pendingCommand.fingerprint !== fingerprint) {
      this.pendingCommand = { fingerprint, requestId: demoRequestId(form.labId ? 'lab-edit' : 'lab-new') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({
        type: form.labId ? 'lab.update' : 'lab.create',
        requestId: this.pendingCommand.requestId,
        payload
      }))
      this.pendingCommand = null
      this.setData({ labForm: null })
      await this.load()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  },

  startCreateDevice() {
    if (this.data.deviceLabOptions.length === 0) {
      this.setData({ error: '请先创建一个在用实验室，再登记设备' })
      return
    }
    this.setData({ deviceForm: { labIndex: 0, code: '', name: '', model: '' }, labForm: null, error: '' })
  },

  onDeviceFormInput(event) {
    const field = event.currentTarget.dataset.field
    if (!field) return
    this.setData({ ['deviceForm.' + field]: event.detail.value })
  },

  onDeviceLabChange(event) {
    this.setData({ 'deviceForm.labIndex': Number(event.detail.value) })
  },

  cancelDeviceForm() {
    this.setData({ deviceForm: null })
  },

  async saveDeviceForm() {
    const form = this.data.deviceForm
    if (!form || this.data.submitting) return
    const activeLabs = this.data.labs.filter((lab) => lab.status === 'ACTIVE')
    const lab = activeLabs[form.labIndex]
    const code = form.code.trim()
    const name = form.name.trim()
    const model = form.model.trim()
    if (!lab) {
      this.setData({ error: '请选择设备所属实验室' })
      return
    }
    if (code.length < 2) {
      this.setData({ error: '设备编号需为 2 至 20 位字母、数字或连字符' })
      return
    }
    if (name.length < 2) {
      this.setData({ error: '设备名称需为 2 至 40 字' })
      return
    }
    const payload = { code, name, labId: lab.id, model }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingCommand || this.pendingCommand.fingerprint !== fingerprint) {
      this.pendingCommand = { fingerprint, requestId: demoRequestId('dev-new') }
    }
    this.setData({ submitting: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type: 'device.create', requestId: this.pendingCommand.requestId, payload }))
      this.pendingCommand = null
      this.setData({ deviceForm: null })
      await this.load()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submitting: false })
    }
  },

  openDevice(event) {
    const id = event.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/device-detail/index?id=' + encodeURIComponent(id) })
  }
})
