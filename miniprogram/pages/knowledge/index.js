const { failureMessage, unwrap } = require('../_shared/ui')

function demoRequestId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

Page({
  data: {
    text: '',
    loading: false,
    searched: false,
    error: '',
    results: [],
    noEvidence: false,
    checkingId: '',
    // 资料管理（教师/管理员的本机演示动作）：导入 / 修订 / 发布
    role: '',
    canManage: false,
    documents: [],
    docForm: null,
    docBusy: false
  },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const core = await getApp().corePromise
      const [session, listValue] = await Promise.all([
        core.query({ type: 'demo.session' }),
        core.query({ type: 'knowledge.document.list' })
      ])
      // 资料维护是教师/管理员的演示动作；角色切换只是本机演示规则，不是真实权限。
      const canManage = session.role === 'teacher' || session.role === 'admin'
      this.setData({
        role: session.role || 'student',
        canManage,
        documents: (unwrap(listValue).items || []).map((item) => ({
          ...item,
          versionText: item.currentVersion || '未发布',
          expiryText: item.effectiveUntil ? '有效期至 ' + item.effectiveUntil.slice(0, 10) : '未设有效期'
        })),
        docForm: canManage ? this.data.docForm : null
      })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    }
  },

  onInput(event) {
    this.setData({ text: event.detail.value })
  },

  async search() {
    const text = this.data.text.trim()
    if (!text) {
      this.setData({ searched: false, results: [], noEvidence: false, error: '请输入设备编号、规程名称或关键词' })
      return
    }
    this.setData({ loading: true, searched: true, error: '', results: [], noEvidence: false })
    try {
      const core = await getApp().corePromise
      const value = unwrap(await core.query({ type: 'knowledge.search', payload: { text } }))
      const results = (value.items || []).map((item) => ({
        ...item,
        sourceText: item.source
          ? [item.source.documentTitle, item.source.version, item.source.section].filter(Boolean).join(' · ')
          : '来源信息未提供'
      }))
      this.setData({ results, noEvidence: value.status === 'NO_EVIDENCE' || results.length === 0 })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 每条引用都能回到它引用的原文版本；被新版替代的历史版本也保留可查。 */
  async verifyCitation(event) {
    const citationId = event.currentTarget.dataset.id
    if (!citationId || this.data.checkingId) return
    this.setData({ checkingId: citationId })
    try {
      const core = await getApp().corePromise
      const value = unwrap(await core.query({ type: 'knowledge.citation', payload: { citationId } }))
      const results = this.data.results.map((item) => {
        if (item.id !== citationId) return { ...item, verify: null }
        if (!value.citation) {
          return { ...item, verify: { ok: false, text: '复核失败：引用指向的原文片段不存在，请勿采信。' } }
        }
        const version = value.version
        const statusText = value.isCurrent ? '现行版本' : '历史版本（已被新版替代，原文保留可查）'
        return {
          ...item,
          verify: {
            ok: true,
            text: [
              `原文一致：${value.citation.text}`,
              `版本：${version ? version.version : '未知'}（${statusText}）`,
              value.document ? `资料：${value.document.title}` : ''
            ].filter(Boolean).join('\n')
          }
        }
      })
      this.setData({ results })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ checkingId: '' })
    }
  },

  startImport() {
    if (!this.data.canManage) return
    this.setData({
      docForm: { mode: 'import', documentId: '', title: '', section: '', text: '', effectiveUntil: '' },
      error: ''
    })
  },

  startRevise(event) {
    if (!this.data.canManage) return
    const document = this.data.documents.find((item) => item.id === event.currentTarget.dataset.id)
    if (!document) return
    this.setData({
      docForm: {
        mode: 'revise', documentId: document.id, title: document.title,
        section: '', text: '', effectiveUntil: document.effectiveUntil || ''
      },
      error: ''
    })
  },

  onDocFormInput(event) {
    const field = event.currentTarget.dataset.field
    if (!field || !this.data.docForm) return
    this.setData({ ['docForm.' + field]: event.detail.value })
  },

  cancelDocForm() {
    this.setData({ docForm: null })
  },

  async saveDocForm() {
    const form = this.data.docForm
    if (!form || this.data.docBusy) return
    if (!this.data.canManage) {
      this.setData({ error: '资料维护仅限演示教师/管理员；角色切换只是本机演示规则，不是真实权限。' })
      return
    }
    const title = form.title.trim()
    const section = form.section.trim()
    const text = form.text.trim()
    if (title.length < 2) {
      this.setData({ error: '资料标题需为 2 至 80 字' })
      return
    }
    if (!section || !text) {
      this.setData({ error: '请填写一个章节名与正文；修订现有资料时只需填写要更新的章节' })
      return
    }
    const payload = form.mode === 'revise'
      ? { documentId: form.documentId, sections: [{ section, text }] }
      : { title, sections: [{ section, text }] }
    if (form.effectiveUntil.trim()) payload.effectiveUntil = form.effectiveUntil.trim()
    const type = form.mode === 'revise' ? 'document.revise' : 'document.import'
    this.setData({ docBusy: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({ type, requestId: demoRequestId(form.mode === 'revise' ? 'doc-revise' : 'doc-new'), payload }))
      this.setData({ docForm: null })
      await this.load()
      wx.showToast({ title: type === 'document.revise' ? '草稿已保存，待发布' : '资料已导入，待发布', icon: 'none' })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ docBusy: false })
    }
  },

  async publishDoc(event) {
    const documentId = event.currentTarget.dataset.id
    if (!documentId || this.data.docBusy) return
    if (!this.data.canManage) {
      this.setData({ error: '资料发布仅限演示教师/管理员；角色切换只是本机演示规则，不是真实权限。' })
      return
    }
    this.setData({ docBusy: true, error: '' })
    try {
      const core = await getApp().corePromise
      unwrap(await core.execute({
        type: 'document.publish',
        requestId: demoRequestId('doc-publish'),
        payload: { documentId }
      }))
      await this.load()
      wx.showToast({ title: '已发布，检索可用现行版本', icon: 'none' })
    } catch (error) {
      this.setData({ error: failureMessage(error) })
    } finally {
      this.setData({ docBusy: false })
    }
  }
})
