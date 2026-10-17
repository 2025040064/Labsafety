const SCHEMA_VERSION = 2;

/** Return a fresh, entirely fictional demo dataset. */
function createSeedSnapshot() {
  return {
    schemaVersion: SCHEMA_VERSION,
    demoRole: 'student',
    labs: [
      { id: 'lab-demo-01', code: 'SIM-LAB-01', name: '模拟实验室 A', location: '虚构校区 A 栋', status: 'ACTIVE' },
      { id: 'lab-demo-02', code: 'SIM-LAB-02', name: '模拟实验室 B', location: '虚构校区 B 栋', status: 'ACTIVE' }
    ],
    devices: [
      { id: 'dev-demo-01', code: 'SIM-DEV-001', labId: 'lab-demo-01', name: '模拟通风柜', model: 'DEMO-FH-01', status: 'ACTIVE' },
      { id: 'dev-demo-02', code: 'SIM-DEV-002', labId: 'lab-demo-01', name: '模拟恒温箱', model: 'DEMO-OV-02', status: 'ACTIVE' },
      { id: 'dev-demo-03', code: 'SIM-DEV-003', labId: 'lab-demo-01', name: '模拟电子天平', model: 'DEMO-BL-03', status: 'ACTIVE' },
      { id: 'dev-demo-04', code: 'SIM-DEV-004', labId: 'lab-demo-02', name: '模拟离心机', model: 'DEMO-CF-04', status: 'ACTIVE' }
    ],
    inspectionTemplates: [
      {
        id: 'tmpl-demo-01', title: '通风柜日常巡检', deviceId: 'dev-demo-01',
        items: ['外观检查', '风速是否正常', '排风管道有无泄漏'], intervalDays: 1
      },
      {
        id: 'tmpl-demo-02', title: '恒温箱日常巡检', deviceId: 'dev-demo-02',
        items: ['温度显示是否正常', '门封条完好性', '内部清洁状态'], intervalDays: 1
      }
    ],
    inspections: [
      { id: 'insp-demo-01', title: '通风柜日常巡检（模拟）', deviceId: 'dev-demo-01', status: 'PLANNED', dueAt: '2026-10-16T09:00:00+08:00' },
      { id: 'insp-demo-02', title: '恒温箱日常巡检（模拟）', deviceId: 'dev-demo-02', status: 'SUBMITTED', dueAt: '2026-10-15T17:00:00+08:00' }
    ],
    incidents: [
      {
        id: 'inc-demo-01', code: 'SIM-INC-001', deviceId: 'dev-demo-02',
        title: '模拟温度显示异常', description: '此记录仅用于演示异常上报流程。',
        severity: 'LOW', status: 'REPORTED', revision: 1,
        createdAt: '2026-10-15T09:00:00+08:00', updatedAt: '2026-10-15T09:00:00+08:00', isDemo: true
      },
      {
        id: 'inc-demo-02', code: 'SIM-INC-002', deviceId: 'dev-demo-01',
        title: '模拟通风柜风速偏低', description: '演示用已确认异常。',
        severity: 'MEDIUM', status: 'CONFIRMED', revision: 2,
        createdAt: '2026-10-14T14:00:00+08:00', updatedAt: '2026-10-14T16:00:00+08:00', isDemo: true
      }
    ],
    events: [
      {
        id: 'evt-demo-01', entityType: 'Incident', entityId: 'inc-demo-01',
        action: 'REPORT', from: null, to: 'REPORTED', actorRole: 'student',
        reason: '模拟上报', at: '2026-10-15T09:00:00+08:00'
      },
      {
        id: 'evt-demo-02', entityType: 'Incident', entityId: 'inc-demo-02',
        action: 'REPORT', from: null, to: 'REPORTED', actorRole: 'student',
        reason: '模拟上报', at: '2026-10-14T14:00:00+08:00'
      },
      {
        id: 'evt-demo-03', entityType: 'Incident', entityId: 'inc-demo-02',
        action: 'CONFIRM', from: 'REPORTED', to: 'CONFIRMED', actorRole: 'teacher',
        reason: '已核实风速偏低', at: '2026-10-14T16:00:00+08:00'
      }
    ],
    documents: [
      { id: 'doc-demo-01', title: '模拟通风柜使用规程', currentVersionId: 'docv-demo-01', isDemo: true },
      { id: 'doc-demo-02', title: '模拟异常上报流程', currentVersionId: 'docv-demo-02', isDemo: true }
    ],
    documentVersions: [
      { id: 'docv-demo-01', documentId: 'doc-demo-01', version: '演示 v1', status: 'CURRENT' },
      { id: 'docv-demo-02', documentId: 'doc-demo-02', version: '演示 v1', status: 'CURRENT' }
    ],
    documentChunks: [
      { id: 'chunk-demo-01', documentVersionId: 'docv-demo-01', section: '使用前检查', text: '使用模拟通风柜前，先检查设备状态与巡检记录。本段仅为功能演示，不构成真实操作规程。' },
      { id: 'chunk-demo-02', documentVersionId: 'docv-demo-02', section: '异常提交', text: '发现模拟设备异常时，关联设备并填写现象；提交后可在异常时间线查看本机演示记录。' }
    ],
    attachments: [],
    receipts: {}
  };
}

module.exports = { SCHEMA_VERSION, createSeedSnapshot };
