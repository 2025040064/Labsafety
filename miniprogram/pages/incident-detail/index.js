const { failureMessage, shortDate, statusView, unwrap, SEVERITY_LABELS } = require('../_shared/ui')

const ROLE_LABELS = { student: '学生', admin: '管理员', teacher: '教师' }
/** 每个状态允许的操作与核心状态机的合法边一一对应，页面本身不能指定任意目标状态。
 *  roles 与核心层 COMMAND_ROLES 一致：状态推进是教师/管理员的演示动作。 */
const ACTION_ROLES = ['teacher', 'admin']
const ACTIONS_BY_STATUS = {
  REPORTED: [
    { type: 'incident.confirm', label: '确认异常', roles: ACTION_ROLES },
    { type: 'incident.reject', label: '驳回异常', danger: true, roles: ACTION_ROLES }
  ],
  CONFIRMED: [
    { type: 'incident.assign', label: '分派处理', roles: ACTION_ROLES }
  ],
  ASSIGNED: [
    { type: 'incident.start', label: '开始处理', roles: ACTION_ROLES }
  ],
  IN_PROGRESS: [
    { type: 'incident.submitReview', label: '提交复核', roles: ACTION_ROLES }
  ],
  PENDING_REVIEW: [
    { type: 'incident.close', label: '复核关闭', roles: ACTION_ROLES },
    { type: 'incident.return', label: '退回处理', danger: true, roles: ACTION_ROLES }
  ]
}
const ACTION_LABELS = {
  REPORT: '上报异常',
  CONFIRM: '确认异常',
  REJECT: '驳回异常',
  ASSIGN: '分派处理',
  START: '开始处理',
  SUBMIT_REVIEW: '提交复核',
  RETURN: '退回处理',
  CLOSE: '复核关闭'
}

Page({
  data: {
    incidentId: '',
    loading: true,
    error: '',
    role: '',
    roleHint: '',
    incident: null,
    events: [],
    attachments: [],
    actions: [],
    ended: false,
    pendingType: '',
    pendingLabel: '',
    pendingReason: '',
    submittingAction: false
  },

  onLoad(options) {
    const incidentId = options.id ? decodeURIComponent(options.id) : ''
    this.setData({ incidentId })
  },

  onShow() {
    this.load()
  },

  async load() {
    if (!this.data.incidentId) {
      this.setData({ loading: false, error: '缺少异常编号' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      const core = await getApp().corePromise
      const [value, session] = await Promise.all([
        core.query({ type: 'incident.detail', payload: { incidentId: this.data.incidentId } }),
        core.query({ type: 'demo.session' })
      ])
      const role = session.role || 'student'
      const roleText = ROLE_LABELS[role] || role
      if (!value.incident) {
        this.setData({ incident: null, events: [], error: '异常记录不存在或演示数据已恢复，请返回工作台。' })
        return
      }
      const incident = value.incident
      const ended = incident.status === 'CLOSED' || incident.status === 'REJECTED'
      const actions = (ACTIONS_BY_STATUS[incident.status] || []).filter((action) => action.roles.includes(role))
      this.setData({
        role,
        roleHint: !ended && actions.length === 0
          ? `当前演示角色为${roleText}，状态推进由教师/管理员执行；角色切换仅是本机演示规则，不是真实权限。`
          : '',
        incident: {
          ...incident,
          codeText: incident.code || incident.id,
          deviceText: incident.deviceName || '未关联设备',
          severityText: SEVERITY_LABELS[String(incident.severity || '').toUpperCase()] || incident.severity || '未分级',
          ...statusView(incident.status)
        },
        actions,
        ended,
        pendingType: '',
        pendingLabel: '',
        pendingReason: '',
        events: (value.events || []).map((event) => ({
          ...event,
          atText: shortDate(event.at),
          actorText: ROLE_LABELS[event.actorRole] || event.actorRole || '本机演示身份',
          actionText: ACTION_LABELS[event.action] || event.action || '状态变化',
          fromText: event.from ? statusView(event.from).statusText : '新建',
          toText: event.to ? statusView(event.to).statusText : '未知',
          reasonText: event.reason || '未填写原因'
        })),
        attachments: (value.attachments || []).map((item) => ({
          ...item,
          sizeText: item.size >= 1024 ? `${Math.max(1, Math.ceil(item.size / 1024))} KB` : `${item.size} B`,
          atText: shortDate(item.recordedAt),
          missing: false
        }))
      })
      this.checkAttachments()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 只保存元数据：图片本体留在本机文件系统，快照与备份都不含二进制。 */
  addAttachment() {
    const incident = this.data.incident
    if (!incident || this.data.submittingAction) return
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      success: (result) => {
        const file = result.tempFiles && result.tempFiles[0]
        if (!file || !file.tempFilePath) return
        wx.getFileSystemManager().saveFile({
          tempFilePath: file.tempFilePath,
          success: async (saved) => {
            this.setData({ submittingAction: true, error: '' })
            try {
              const core = await getApp().corePromise
              unwrap(await core.execute({
                type: 'attachment.record',
                requestId: 'att-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10),
                payload: {
                  entityType: 'Incident',
                  entityId: incident.id,
                  fileName: '现场图片-' + new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-') + '.jpg',
                  size: file.size || 0,
                  localPath: saved.savedFilePath
                }
              }))
              await this.load()
              wx.showToast({ title: '附件已记录', icon: 'success' })
            } catch (error) {
              this.setData({ error: failureMessage(error) })
            } finally {
              this.setData({ submittingAction: false })
            }
          },
          fail: () => {
            this.setData({ error: '图片转存失败，请重试' })
          }
        })
      }
    })
  },

  /** 存在性检查：异机恢复的备份没有图片文件，界面明确提示缺失。 */
  checkAttachments() {
    const fs = wx.getFileSystemManager && wx.getFileSystemManager()
    if (!fs || typeof fs.access !== 'function') return
    this.data.attachments.forEach((item, index) => {
      if (!item.localPath) {
        this.setData({ ['attachments[' + index + '].existsText']: '未关联本机文件' })
        return
      }
      fs.access({
        path: item.localPath,
        success: () => this.setData({ ['attachments[' + index + '].existsText']: '图片文件在本机存在', ['attachments[' + index + '].missing']: false }),
        fail: () => this.setData({ ['attachments[' + index + '].existsText']: '附件缺失：可能是异机恢复的备份，请重新选择图片', ['attachments[' + index + '].missing']: true })
      })
    })
  },

  selectAction(event) {
    const type = event.currentTarget.dataset.type
    const action = this.data.actions.find((item) => item.type === type)
    if (!action || this.data.submittingAction) return
    this.setData({ pendingType: type, pendingLabel: action.label, pendingReason: '', error: '' })
  },

  onReasonInput(event) {
    this.setData({ pendingReason: event.detail.value })
  },

  cancelAction() {
    this.setData({ pendingType: '', pendingLabel: '', pendingReason: '' })
  },

  async submitAction() {
    const incident = this.data.incident
    const type = this.data.pendingType
    if (!incident || !type || this.data.submittingAction) return
    const reason = this.data.pendingReason.trim()
    if (reason.length < 2) {
      this.setData({ error: '请填写至少 2 个字的处理说明' })
      return
    }
    const payload = { incidentId: incident.id, reason, expectedRevision: incident.revision }
    const fingerprint = JSON.stringify(payload)
    if (!this.pendingCommand || this.pendingCommand.fingerprint !== fingerprint) {
      this.pendingCommand = {
        fingerprint,
        requestId: 'act-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
      }
    }
    this.setData({ submittingAction: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type, requestId: this.pendingCommand.requestId, payload }))
      this.pendingCommand = null
      this.cancelAction()
      await this.load()
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ submittingAction: false })
    }
  }
})
