const { createSeedSnapshot, SCHEMA_VERSION } = require('./seed');
const { canonicalStringify, sha256 } = require('./hash');
// P0-3：预检与真实写入共用同一口径的容量判定，序列化逻辑收敛在仓库接缝内。
const { slotRecordBytes, MAX_RECORD_BYTES } = require('../infrastructure/WxLocalRepository');

const ROLES = Object.freeze({ student: '学生', teacher: '教师', admin: '管理员' });
const SEVERITIES = new Set(['LOW', 'MEDIUM', 'HIGH']);

/**
 * Incident state machine: one command name per legal edge. Any status outside
 * `allowedFrom` is rejected, so an incident can never skip a step or reopen silently.
 */
const INCIDENT_TRANSITIONS = Object.freeze({
  'incident.confirm':      { action: 'CONFIRM',       allowedFrom: ['REPORTED'],       target: 'CONFIRMED' },
  'incident.reject':       { action: 'REJECT',        allowedFrom: ['REPORTED'],       target: 'REJECTED' },
  'incident.assign':       { action: 'ASSIGN',        allowedFrom: ['CONFIRMED'],      target: 'ASSIGNED' },
  'incident.start':        { action: 'START',         allowedFrom: ['ASSIGNED'],       target: 'IN_PROGRESS' },
  'incident.submitReview': { action: 'SUBMIT_REVIEW', allowedFrom: ['IN_PROGRESS'],    target: 'PENDING_REVIEW' },
  'incident.return':       { action: 'RETURN',        allowedFrom: ['PENDING_REVIEW'], target: 'IN_PROGRESS' },
  'incident.close':        { action: 'CLOSE',         allowedFrom: ['PENDING_REVIEW'], target: 'CLOSED' }
});

const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const CODE_PATTERN = /^[A-Za-z0-9-]{2,20}$/;

/**
 * 演示角色矩阵（本机演示规则，不是真实访问控制）：学生可上报异常、提交巡检与记录附件；
 * 教师另可推进异常状态与维护资料；管理员另可编辑台账。真实授权留给 G5 服务端实现。
 */
const COMMAND_ROLES = Object.freeze({
  'incident.report': ['student', 'teacher', 'admin'],
  'inspection.submit': ['student', 'teacher', 'admin'],
  'attachment.record': ['student', 'teacher', 'admin'],
  'incident.confirm': ['teacher', 'admin'],
  'incident.reject': ['teacher', 'admin'],
  'incident.assign': ['teacher', 'admin'],
  'incident.start': ['teacher', 'admin'],
  'incident.submitReview': ['teacher', 'admin'],
  'incident.return': ['teacher', 'admin'],
  'incident.close': ['teacher', 'admin'],
  'document.import': ['teacher', 'admin'],
  'document.revise': ['teacher', 'admin'],
  'document.publish': ['teacher', 'admin'],
  'lab.create': ['admin'],
  'lab.update': ['admin'],
  'device.create': ['admin'],
  'device.update': ['admin'],
  'device.disable': ['admin'],
  'device.enable': ['admin']
});

const BACKUP_KIND = 'labsafe-demo-backup';
const BACKUP_VERSION = 1;
/** 导出时的容量提示阈值，与仓库单键余量一致；恢复阻断则直接用 MAX_RECORD_BYTES。 */
const BACKUP_WARN_BYTES = 900 * 1024;
const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;

const INCIDENT_STATUSES = new Set(['REPORTED', 'CONFIRMED', 'ASSIGNED', 'IN_PROGRESS', 'PENDING_REVIEW', 'CLOSED', 'REJECTED']);
const INSPECTION_STATUSES = new Set(['PLANNED', 'SUBMITTED', 'CANCELLED']);
const VERSION_STATUSES = new Set(['DRAFT', 'CURRENT', 'SUPERSEDED']);

/** Optional list filters. A key missing from the filter object is simply not applied. */
const DEVICE_FILTERS = Object.freeze({
  labId: (device, expected) => device.labId === String(expected),
  status: (device, expected) => device.status === String(expected),
  keyword: (device, expected) => `${device.name} ${device.code}`.toLowerCase().includes(String(expected).trim().toLowerCase())
});
const INCIDENT_FILTERS = Object.freeze({
  status: (incident, expected) => incident.status === String(expected),
  deviceId: (incident, expected) => incident.deviceId === String(expected),
  severity: (incident, expected) => String(incident.severity || '').toUpperCase() === String(expected).trim().toUpperCase()
});
const INSPECTION_FILTERS = Object.freeze({
  status: (inspection, expected) => inspection.status === String(expected),
  deviceId: (inspection, expected) => inspection.deviceId === String(expected)
});

function failure(code, message) { return { ok: false, error: { code, message } }; }
function success(data) { return { ok: true, data }; }
function domainError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

/** Version 1 snapshots stay readable so an existing local demo can still be opened and reset. */
function assertSnapshot(snapshot) {
  if (!snapshot || (snapshot.schemaVersion !== SCHEMA_VERSION && snapshot.schemaVersion !== 1)) {
    throw domainError('SCHEMA_UNSUPPORTED', '本机数据版本不受当前程序支持，请先备份并升级。');
  }
}

/** `true` when the caller explicitly supplied the field; undefined/null means "leave untouched". */
function isProvided(value) { return value !== undefined && value !== null; }

function nonBlankText(value) { return typeof value === 'string' && value.trim().length > 0; }

function validDateTime(value) { return nonBlankText(value) && Number.isFinite(Date.parse(value)); }

/** UTF-8 byte length for capacity warnings; mirrors the repository's own accounting. */
function utf8ByteLength(input) {
  let size = 0;
  for (const character of input) {
    const code = character.codePointAt(0);
    size += code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
  }
  return size;
}

/**
 * Bring an accepted older snapshot up to the current schema in place. Version 1 lacks
 * inspection templates, the knowledge base and attachments; those become empty collections
 * so old data stays readable and the next write naturally carries the new schema version.
 */
function migrateSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot) || snapshot.schemaVersion === SCHEMA_VERSION) {
    return { snapshot, migrated: false };
  }
  if (snapshot.schemaVersion !== 1) return { snapshot, migrated: false };
  snapshot.schemaVersion = SCHEMA_VERSION;
  if (!Array.isArray(snapshot.inspectionTemplates)) snapshot.inspectionTemplates = [];
  if (!Array.isArray(snapshot.documents)) snapshot.documents = [];
  if (!Array.isArray(snapshot.documentVersions)) snapshot.documentVersions = [];
  if (!Array.isArray(snapshot.documentChunks)) snapshot.documentChunks = [];
  if (!Array.isArray(snapshot.attachments)) snapshot.attachments = [];
  return { snapshot, migrated: true };
}

/** Fields added without a schema bump are filled only when absent; bad explicit values still fail validation. */
function applyLegacyDefaults(snapshot) {
  let applied = false;
  if (snapshot.demoRole === undefined) { snapshot.demoRole = 'student'; applied = true; }
  if (snapshot.receipts === undefined) { snapshot.receipts = {}; applied = true; }
  return applied;
}

/** Trim a string field and enforce inclusive length bounds; returns null when invalid. */
function boundedText(value, min, max) {
  const text = typeof value === 'string' ? value.trim() : '';
  return text.length >= min && text.length <= max ? text : null;
}

function normalizeCode(value) { return typeof value === 'string' ? value.trim().toUpperCase() : ''; }

/** Case-insensitive ledger code uniqueness, optionally ignoring the record being edited. */
function codeTaken(items, code, excludeId) {
  return items.some(item => item.id !== excludeId && String(item.code || '').toUpperCase() === code);
}

/** Schema version 1 has no templates; every reader degrades to an empty list. */
function templatesOf(snapshot) {
  return Array.isArray(snapshot.inspectionTemplates) ? snapshot.inspectionTemplates : [];
}

/** Each imported document needs a title plus at least one titled, bounded section. */
function parseSections(input) {
  if (!Array.isArray(input) || input.length === 0) return null;
  const sections = [];
  for (const entry of input) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    const section = boundedText(entry.section, 1, 60);
    const text = boundedText(entry.text, 1, 2000);
    if (section === null || text === null) return null;
    sections.push({ section, text });
  }
  return sections;
}

/** Optional ISO effective-until bound; an unparsable date is a validation failure. */
function parseExpiry(value) {
  if (!isProvided(value) || value === '') return { expiry: null };
  const text = String(value).trim();
  return Number.isFinite(new Date(text).getTime()) ? { expiry: text } : null;
}

/** Search must ignore versions that were replaced or whose effective time has passed. */
function versionInForce(version, nowText) {
  if (!version || version.status !== 'CURRENT') return false;
  if (version.effectiveUntil && new Date(version.effectiveUntil).getTime() < new Date(nowText).getTime()) return false;
  return true;
}

/** Chunk count of a document's versions; the next revision label is v<n+1>. */
function nextVersionLabel(snapshot, documentId) {
  const count = snapshot.documentVersions.filter(item => item.documentId === documentId).length;
  return `v${count + 1}`;
}

/** Local rule hints for the assistant draft parser; keywords, not an AI model. */
const INCIDENT_HINTS = ['异常', '故障', '损坏', '坏了', '泄漏', '漏电', '报警', '失效', '偏低', '偏高', '异味', '冒烟', '上报'];

/**
 * P0-2：备份快照的深度不变量校验，backup.precheck 与 backup.restore 共用同一份规则。
 * 校验值只能证明字节未被篡改，不能证明业务引用仍然成立；本函数在 schema 迁移之后
 * 对目标 v2 快照做结构与引用检查，任何一条不满足都拒绝进入业务层。
 */
function validateSnapshotInvariants(snapshot) {
  const collections = ['labs', 'devices', 'inspections', 'incidents', 'events',
    'inspectionTemplates', 'documents', 'documentVersions', 'documentChunks', 'attachments'];
  for (const key of collections) {
    if (!Array.isArray(snapshot[key])) {
      return failure('BACKUP_INVALID', `备份快照缺少集合 ${key}，不是完整的本机数据。`);
    }
  }
  if (!snapshot.receipts || typeof snapshot.receipts !== 'object' || Array.isArray(snapshot.receipts)) {
    return failure('BACKUP_INVALID', '备份快照缺少回执记录，不是完整的本机数据。');
  }
  for (const [requestId, receipt] of Object.entries(snapshot.receipts)) {
    if (!REQUEST_ID_PATTERN.test(requestId) || !receipt || typeof receipt !== 'object' || Array.isArray(receipt) ||
        typeof receipt.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(receipt.fingerprint) ||
        !receipt.data || typeof receipt.data !== 'object' || Array.isArray(receipt.data)) {
      return failure('BACKUP_INVALID', `备份快照的请求回执 ${requestId} 无效。`);
    }
  }
  if (!Object.prototype.hasOwnProperty.call(ROLES, snapshot.demoRole)) {
    return failure('BACKUP_INVALID', '备份快照缺少有效的演示角色。');
  }
  const index = {};
  for (const key of collections) {
    const seen = new Set();
    for (const item of snapshot[key]) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) {
        return failure('BACKUP_INVALID', `集合 ${key} 中存在无效记录。`);
      }
      if (typeof item.id !== 'string' || !item.id) {
        return failure('BACKUP_INVALID', `集合 ${key} 中存在缺少编号的记录。`);
      }
      if (seen.has(item.id)) return failure('BACKUP_INVALID', `集合 ${key} 中存在重复编号 ${item.id}。`);
      seen.add(item.id);
    }
    index[key] = seen;
  }
  const has = (key, id) => index[key].has(id);

  const labCodes = new Set();
  for (const lab of snapshot.labs) {
    if (!nonBlankText(lab.code) || !nonBlankText(lab.name) ||
        (lab.location != null && typeof lab.location !== 'string')) {
      return failure('BACKUP_INVALID', `实验室 ${lab.id} 缺少有效的编号或名称。`);
    }
    const code = lab.code.trim().toUpperCase();
    if (labCodes.has(code)) return failure('BACKUP_INVALID', `实验室编号 ${lab.code} 重复。`);
    labCodes.add(code);
    if (!['ACTIVE', 'INACTIVE'].includes(lab.status)) {
      return failure('BACKUP_INVALID', `实验室 ${lab.id} 处于非法状态 ${lab.status}。`);
    }
  }
  const deviceCodes = new Set();
  for (const device of snapshot.devices) {
    if (!nonBlankText(device.code) || !nonBlankText(device.name) ||
        (device.model != null && typeof device.model !== 'string')) {
      return failure('BACKUP_INVALID', `设备 ${device.id} 缺少有效的编号或名称。`);
    }
    const code = device.code.trim().toUpperCase();
    if (deviceCodes.has(code)) return failure('BACKUP_INVALID', `设备编号 ${device.code} 重复。`);
    deviceCodes.add(code);
    if (!['ACTIVE', 'INACTIVE'].includes(device.status)) {
      return failure('BACKUP_INVALID', `设备 ${device.id} 处于非法状态 ${device.status}。`);
    }
    if (!has('labs', device.labId)) return failure('BACKUP_INVALID', `设备 ${device.id} 指向不存在的实验室。`);
  }
  for (const template of snapshot.inspectionTemplates) {
    if (!has('devices', template.deviceId)) return failure('BACKUP_INVALID', `巡检模板 ${template.id} 指向不存在的设备。`);
    if (!nonBlankText(template.title) ||
        (template.intervalDays != null && (!Number.isSafeInteger(template.intervalDays) || template.intervalDays < 1))) {
      return failure('BACKUP_INVALID', `巡检模板 ${template.id} 的标题或周期无效。`);
    }
    if (!Array.isArray(template.items) || template.items.length === 0 ||
        !template.items.every(item => typeof item === 'string' && item.trim())) {
      return failure('BACKUP_INVALID', `巡检模板 ${template.id} 的检查项无效。`);
    }
  }
  for (const inspection of snapshot.inspections) {
    if (!has('devices', inspection.deviceId)) return failure('BACKUP_INVALID', `巡检 ${inspection.id} 指向不存在的设备。`);
    if (!nonBlankText(inspection.title)) {
      return failure('BACKUP_INVALID', `巡检 ${inspection.id} 缺少有效的标题。`);
    }
    if ((inspection.dueAt != null && !validDateTime(inspection.dueAt)) ||
        (inspection.submittedAt != null && !validDateTime(inspection.submittedAt)) ||
        (inspection.results != null && (!Array.isArray(inspection.results) ||
          !inspection.results.every(result => result && typeof result === 'object' && !Array.isArray(result) &&
            nonBlankText(result.item) && typeof result.passed === 'boolean' &&
            (result.note == null || typeof result.note === 'string'))))) {
      return failure('BACKUP_INVALID', `巡检 ${inspection.id} 的时间或结果无效。`);
    }
    if (inspection.templateId != null) {
      const template = snapshot.inspectionTemplates.find(item => item.id === inspection.templateId);
      if (!template || template.deviceId !== inspection.deviceId) {
        return failure('BACKUP_INVALID', `巡检 ${inspection.id} 的模板与设备不匹配。`);
      }
    }
    if (!INSPECTION_STATUSES.has(inspection.status)) {
      return failure('BACKUP_INVALID', `巡检 ${inspection.id} 处于非法状态 ${inspection.status}。`);
    }
  }
  for (const incident of snapshot.incidents) {
    if (!has('devices', incident.deviceId)) return failure('BACKUP_INVALID', `异常 ${incident.id} 指向不存在的设备。`);
    if (!nonBlankText(incident.code) || !nonBlankText(incident.title) ||
        typeof incident.description !== 'string' || !SEVERITIES.has(incident.severity) ||
        !validDateTime(incident.createdAt) || !validDateTime(incident.updatedAt)) {
      return failure('BACKUP_INVALID', `异常 ${incident.id} 的编号、内容或时间无效。`);
    }
    if (!INCIDENT_STATUSES.has(incident.status)) {
      return failure('BACKUP_INVALID', `异常 ${incident.id} 处于非法状态 ${incident.status}。`);
    }
    if (!Number.isSafeInteger(incident.revision) || incident.revision < 1) {
      return failure('BACKUP_INVALID', `异常 ${incident.id} 的记录版本无效。`);
    }
  }
  const entityIndex = { Incident: 'incidents', Inspection: 'inspections' };
  for (const event of snapshot.events) {
    if (!nonBlankText(event.action)) {
      return failure('BACKUP_INVALID', `事件 ${event.id} 缺少有效的动作。`);
    }
    if (!validDateTime(event.at)) {
      return failure('BACKUP_INVALID', `事件 ${event.id} 缺少有效的时间。`);
    }
    if ((event.from != null && !nonBlankText(event.from)) || !nonBlankText(event.to) ||
        !Object.prototype.hasOwnProperty.call(ROLES, event.actorRole) || !nonBlankText(event.reason)) {
      return failure('BACKUP_INVALID', `事件 ${event.id} 的状态、角色或说明无效。`);
    }
    if (!Object.prototype.hasOwnProperty.call(entityIndex, event.entityType)) {
      return failure('BACKUP_INVALID', `事件 ${event.id} 指向不支持的实体类型。`);
    }
    const collection = entityIndex[event.entityType];
    if (!has(collection, event.entityId)) return failure('BACKUP_INVALID', `事件 ${event.id} 指向不存在的记录。`);
  }
  const currentVersions = new Map();
  const draftVersions = new Set();
  const versionsWithChunks = new Set(snapshot.documentChunks.map(chunk => chunk.documentVersionId));
  for (const version of snapshot.documentVersions) {
    if (!has('documents', version.documentId)) return failure('BACKUP_INVALID', `资料版本 ${version.id} 指向不存在的资料。`);
    if (!versionsWithChunks.has(version.id)) return failure('BACKUP_INVALID', `资料版本 ${version.id} 缺少正文片段。`);
    if (typeof version.version !== 'string' || !version.version.trim()) {
      return failure('BACKUP_INVALID', `资料版本 ${version.id} 缺少版本标识。`);
    }
    if (!VERSION_STATUSES.has(version.status)) {
      return failure('BACKUP_INVALID', `资料版本 ${version.id} 处于非法状态 ${version.status}。`);
    }
    if (version.effectiveUntil != null &&
        (typeof version.effectiveUntil !== 'string' || !Number.isFinite(Date.parse(version.effectiveUntil)))) {
      return failure('BACKUP_INVALID', `资料版本 ${version.id} 的有效期格式无效。`);
    }
    if (version.status === 'CURRENT') {
      if (currentVersions.has(version.documentId)) {
        return failure('BACKUP_INVALID', `资料 ${version.documentId} 存在多个现行版本。`);
      }
      currentVersions.set(version.documentId, version.id);
    }
    if (version.status === 'DRAFT') {
      if (draftVersions.has(version.documentId)) {
        return failure('BACKUP_INVALID', `资料 ${version.documentId} 存在多个待发布版本。`);
      }
      draftVersions.add(version.documentId);
    }
  }
  for (const document of snapshot.documents) {
    if (typeof document.title !== 'string' || !document.title.trim()) {
      return failure('BACKUP_INVALID', `资料 ${document.id} 缺少有效的标题。`);
    }
    if (document.currentVersionId == null) {
      if (currentVersions.has(document.id)) {
        return failure('BACKUP_INVALID', `资料 ${document.id} 缺少现行版本引用。`);
      }
    } else {
      const version = snapshot.documentVersions.find(item => item.id === document.currentVersionId);
      if (!version || version.documentId !== document.id || version.status !== 'CURRENT' ||
          currentVersions.get(document.id) !== version.id) {
        return failure('BACKUP_INVALID', `资料 ${document.id} 的当前版本引用无效。`);
      }
    }
  }
  for (const chunk of snapshot.documentChunks) {
    if (!has('documentVersions', chunk.documentVersionId)) {
      return failure('BACKUP_INVALID', `章节片段 ${chunk.id} 指向不存在的资料版本。`);
    }
    if (typeof chunk.section !== 'string' || !chunk.section.trim() ||
        typeof chunk.text !== 'string' || !chunk.text.trim()) {
      return failure('BACKUP_INVALID', `章节片段 ${chunk.id} 缺少章节名或正文。`);
    }
  }
  for (const attachment of snapshot.attachments) {
    if (attachment.entityType !== 'Incident' || !has('incidents', attachment.entityId)) {
      return failure('BACKUP_INVALID', `附件 ${attachment.id} 指向不存在的异常记录。`);
    }
    if (!nonBlankText(attachment.fileName) ||
        !Number.isSafeInteger(attachment.size) || attachment.size < 0 || attachment.size > ATTACHMENT_MAX_BYTES ||
        !validDateTime(attachment.recordedAt) ||
        (attachment.localPath != null && typeof attachment.localPath !== 'string')) {
      return failure('BACKUP_INVALID', `附件 ${attachment.id} 的文件名、大小、路径或时间无效。`);
    }
  }
  return success({ snapshot });
}

/** Receipt bookkeeping that is repaired when absent and never reads an inherited key. */
function receiptsOf(snapshot) {
  if (!snapshot.receipts || typeof snapshot.receipts !== 'object' || Array.isArray(snapshot.receipts)) snapshot.receipts = {};
  return snapshot.receipts;
}

/** Attachment metadata is repaired when absent; binaries never live in the snapshot. */
function attachmentsOf(snapshot) {
  if (!Array.isArray(snapshot.attachments)) snapshot.attachments = [];
  return snapshot.attachments;
}

function receiptOf(snapshot, requestId) {
  const receipts = receiptsOf(snapshot);
  return Object.prototype.hasOwnProperty.call(receipts, requestId) ? receipts[requestId] : null;
}

function deviceView(device, snapshot) {
  const lab = snapshot.labs.find(item => item.id === device.labId);
  return { ...device, labName: lab ? lab.name : '未知实验室' };
}

function incidentView(incident, snapshot) {
  const device = snapshot.devices.find(item => item.id === incident.deviceId);
  return { ...incident, deviceName: device ? device.name : '未知设备' };
}

function inspectionView(inspection, snapshot) {
  const device = snapshot.devices.find(item => item.id === inspection.deviceId);
  return { ...inspection, deviceName: device ? device.name : '未知设备' };
}

function labView(lab, snapshot) {
  return { ...lab, deviceCount: snapshot.devices.filter(item => item.labId === lab.id).length };
}

/** Stable timeline order: by time, then by id, so replays never reshuffle history. */
function entityEvents(snapshot, entityType, entityId) {
  return snapshot.events
    .filter(item => item.entityType === entityType && item.entityId === entityId)
    .sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id));
}

/** Attachment metadata for one entity, same stable ordering rule as events. */
function entityAttachments(snapshot, entityType, entityId) {
  return attachmentsOf(snapshot)
    .filter(item => item.entityType === entityType && item.entityId === entityId)
    .sort((a, b) => String(a.recordedAt).localeCompare(String(b.recordedAt)) || a.id.localeCompare(b.id));
}

/** Apply optional filters; a missing or empty filter keeps the original unfiltered contract. */
function filterItems(items, filter, matchers) {
  if (!filter || typeof filter !== 'object' || Array.isArray(filter)) return items;
  return items.filter(item => Object.keys(matchers).every(key => {
    const expected = filter[key];
    if (!isProvided(expected) || expected === '') return true;
    return matchers[key](item, expected);
  }));
}

/**
 * Business boundary for the local demo. Pages know only query/execute/maintenance;
 * a future server-side core may keep that page contract while moving write authority
 * and validation to the server (not by sending whole snapshots over the network).
 * @param {{repository: {load: Function, commit: Function}, clock?: Function, createId?: Function}} options
 */
function createLabSafeCore({ repository, clock = () => new Date().toISOString(), createId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}` }) {
  if (!repository || typeof repository.load !== 'function' || typeof repository.commit !== 'function') {
    throw new Error('repository 必须实现 load 和 commit');
  }

  async function read() {
    const state = await repository.load();
    assertSnapshot(state.snapshot);
    // 迁移只在内存视图完成：旧快照可读，下一次写入自然携带新 schemaVersion，不产生额外提交。
    const snapshot = migrateSnapshot(state.snapshot).snapshot;
    applyLegacyDefaults(snapshot);
    return { generation: state.generation, snapshot };
  }

  /**
   * Shared write path for every idempotent command: validate the requestId, replay a
   * stored receipt when the same requestId and fingerprint return, let `apply` validate
   * and mutate a private snapshot copy, then commit the aggregate once. A failed
   * `apply` never commits, and a generation conflict re-reads the winner's receipt.
   * @param {{type: string, requestId: unknown, normalized: object, apply: (snapshot: object) => object}} command
   * @returns {Promise<{ok: boolean, data?: object, error?: {code: string, message: string}}>}
   */
  async function runCommand({ type, requestId, normalized, apply }) {
    if (typeof requestId !== 'string' || !REQUEST_ID_PATTERN.test(requestId)) {
      return failure('INVALID_REQUEST_ID', '请求编号只能使用 1 至 128 位英文字母、数字、下划线或连字符。');
    }
    const fingerprint = sha256(canonicalStringify({ type, payload: normalized }));
    const { generation, snapshot } = await read();
    // 演示角色矩阵在业务校验之前拦截：页面只看得到对应角色可用的操作。
    const allowedRoles = COMMAND_ROLES[type];
    if (allowedRoles && !allowedRoles.includes(snapshot.demoRole)) {
      return failure('ROLE_FORBIDDEN', `当前演示角色（${ROLES[snapshot.demoRole] || snapshot.demoRole}）无权执行该操作；这是本机演示规则，不是真实权限控制。`);
    }
    const existing = receiptOf(snapshot, requestId);
    if (existing) {
      if (existing.fingerprint !== fingerprint) return failure('REQUEST_ID_REUSED', '同一请求编号不能用于不同内容。');
      return success(existing.data);
    }
    const applied = apply(snapshot);
    if (!applied.ok) return applied;
    receiptsOf(snapshot)[requestId] = { fingerprint, data: applied.data };
    try {
      await repository.commit(generation, snapshot);
    } catch (error) {
      if (error.code === 'GENERATION_CONFLICT') {
        const receipt = receiptOf((await read()).snapshot, requestId);
        if (receipt && receipt.fingerprint === fingerprint) return success(receipt.data);
      }
      throw error;
    }
    return success(applied.data);
  }

  /** incident.report — the only command that creates an Incident, always at REPORTED. */
  function reportIncident(payload, requestId) {
    const deviceId = String(payload.deviceId || '');
    const title = boundedText(payload.title, 2, 80);
    const description = boundedText(payload.description, 0, 1000);
    const severity = String(payload.severity || 'LOW').toUpperCase();
    if (!deviceId || title === null || description === null || !SEVERITIES.has(severity)) {
      return failure('INVALID_INPUT', '请填写设备、2 至 80 字标题和有效严重程度；描述不超过 1000 字。');
    }
    return runCommand({
      type: 'incident.report', requestId, normalized: { deviceId, title, description, severity },
      apply: snapshot => {
        const device = snapshot.devices.find(item => item.id === deviceId && item.status === 'ACTIVE');
        if (!device) return failure('DEVICE_UNAVAILABLE', '设备不存在或不可上报。');
        const now = clock();
        const incident = {
          id: `inc-${createId()}`, code: `SIM-INC-${String(snapshot.incidents.length + 1).padStart(3, '0')}`,
          deviceId, title, description, severity, status: 'REPORTED', revision: 1,
          createdAt: now, updatedAt: now, isDemo: true
        };
        if (snapshot.incidents.some(item => item.id === incident.id)) return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        snapshot.incidents.push(incident);
        snapshot.events.push({
          id: `evt-${createId()}`, entityType: 'Incident', entityId: incident.id,
          action: 'REPORT', from: null, to: 'REPORTED', actorRole: snapshot.demoRole,
          reason: '模拟上报', at: now
        });
        return success({ incident: incidentView(incident, snapshot) });
      }
    });
  }

  /**
   * One incident state machine edge: precondition, optional revision guard, status change,
   * revision bump and an immutable event written in the same commit.
   * @param {string} type command name listed in INCIDENT_TRANSITIONS
   */
  function transitionIncident(type, payload, requestId) {
    const transition = INCIDENT_TRANSITIONS[type];
    const incidentId = String(payload.incidentId || '');
    const reason = boundedText(payload.reason, 2, 500);
    if (!incidentId || reason === null) {
      return failure('INVALID_INPUT', '请填写异常编号，并给出 2 至 500 字的处理说明。');
    }
    const guarded = isProvided(payload.expectedRevision);
    if (guarded && !Number.isSafeInteger(payload.expectedRevision)) {
      return failure('INVALID_INPUT', 'expectedRevision 必须是整数版本号。');
    }
    const normalized = guarded
      ? { incidentId, reason, expectedRevision: payload.expectedRevision }
      : { incidentId, reason };
    return runCommand({
      type, requestId, normalized,
      apply: snapshot => {
        const incident = snapshot.incidents.find(item => item.id === incidentId);
        if (!incident) return failure('INCIDENT_NOT_FOUND', '异常记录不存在或演示数据已恢复。');
        if (!transition.allowedFrom.includes(incident.status)) {
          return failure('INVALID_TRANSITION', `异常当前处于 ${incident.status}，不能执行 ${transition.action}。`);
        }
        if (guarded && incident.revision !== payload.expectedRevision) {
          return failure('REVISION_CONFLICT', `异常已更新到版本 ${incident.revision}，请刷新后重试。`);
        }
        const now = clock();
        const event = {
          id: `evt-${createId()}`, entityType: 'Incident', entityId: incident.id,
          action: transition.action, from: incident.status, to: transition.target,
          actorRole: snapshot.demoRole, reason, at: now
        };
        if (snapshot.events.some(item => item.id === event.id)) return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        incident.status = transition.target;
        incident.revision += 1;
        incident.updatedAt = now;
        snapshot.events.push(event);
        return success({ incident: incidentView(incident, snapshot), event });
      }
    });
  }

  /** lab.create — ledger code is unique across labs, case-insensitively. */
  function createLab(payload, requestId) {
    const code = normalizeCode(payload.code);
    const name = boundedText(payload.name, 2, 40);
    const location = isProvided(payload.location) ? boundedText(payload.location, 0, 60) : '';
    if (!CODE_PATTERN.test(code) || name === null || location === null) {
      return failure('INVALID_INPUT', '实验室编号需为 2 至 20 位字母、数字或连字符，名称 2 至 40 字，位置不超过 60 字。');
    }
    return runCommand({
      type: 'lab.create', requestId, normalized: { code, name, location },
      apply: snapshot => {
        if (codeTaken(snapshot.labs, code)) return failure('CODE_DUPLICATE', `实验室编号 ${code} 已存在。`);
        const lab = { id: `lab-${createId()}`, code, name, location, status: 'ACTIVE' };
        if (snapshot.labs.some(item => item.id === lab.id)) return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        snapshot.labs.push(lab);
        return success({ lab: labView(lab, snapshot) });
      }
    });
  }

  /** lab.update — only the supplied fields change; history keeps referencing the same lab id. */
  function updateLab(payload, requestId) {
    const labId = String(payload.labId || '');
    if (!labId) return failure('INVALID_INPUT', '请指定要修改的实验室。');
    const changes = {};
    if (isProvided(payload.code)) {
      changes.code = normalizeCode(payload.code);
      if (!CODE_PATTERN.test(changes.code)) return failure('INVALID_INPUT', '实验室编号需为 2 至 20 位字母、数字或连字符。');
    }
    if (isProvided(payload.name)) {
      changes.name = boundedText(payload.name, 2, 40);
      if (changes.name === null) return failure('INVALID_INPUT', '实验室名称需为 2 至 40 字。');
    }
    if (isProvided(payload.location)) {
      changes.location = boundedText(payload.location, 0, 60);
      if (changes.location === null) return failure('INVALID_INPUT', '实验室位置不超过 60 字。');
    }
    if (!Object.keys(changes).length) return failure('INVALID_INPUT', '请至少填写一个要修改的字段。');
    return runCommand({
      type: 'lab.update', requestId, normalized: { labId, ...changes },
      apply: snapshot => {
        const lab = snapshot.labs.find(item => item.id === labId);
        if (!lab) return failure('LAB_NOT_FOUND', '实验室不存在或演示数据已恢复。');
        if (changes.code && codeTaken(snapshot.labs, changes.code, labId)) {
          return failure('CODE_DUPLICATE', `实验室编号 ${changes.code} 已存在。`);
        }
        Object.assign(lab, changes);
        return success({ lab: labView(lab, snapshot) });
      }
    });
  }

  /** device.create — the device must belong to an existing, active lab. */
  function createDevice(payload, requestId) {
    const code = normalizeCode(payload.code);
    const name = boundedText(payload.name, 2, 40);
    const labId = String(payload.labId || '');
    const model = isProvided(payload.model) ? boundedText(payload.model, 0, 40) : '';
    if (!CODE_PATTERN.test(code) || name === null || !labId || model === null) {
      return failure('INVALID_INPUT', '设备编号需为 2 至 20 位字母、数字或连字符，名称 2 至 40 字，并选择所属实验室。');
    }
    return runCommand({
      type: 'device.create', requestId, normalized: { code, name, labId, model },
      apply: snapshot => {
        const lab = snapshot.labs.find(item => item.id === labId);
        if (!lab) return failure('LAB_NOT_FOUND', '所属实验室不存在或演示数据已恢复。');
        if (lab.status !== 'ACTIVE') return failure('LAB_INACTIVE', '实验室已停用，不能再登记设备。');
        if (codeTaken(snapshot.devices, code)) return failure('CODE_DUPLICATE', `设备编号 ${code} 已存在。`);
        const device = { id: `dev-${createId()}`, code, labId, name, model, status: 'ACTIVE' };
        if (snapshot.devices.some(item => item.id === device.id)) return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        snapshot.devices.push(device);
        return success({ device: deviceView(device, snapshot) });
      }
    });
  }

  /** device.update — a disabled device is read-only so closed history stays reproducible. */
  function updateDevice(payload, requestId) {
    const deviceId = String(payload.deviceId || '');
    if (!deviceId) return failure('INVALID_INPUT', '请指定要修改的设备。');
    const changes = {};
    if (isProvided(payload.code)) {
      changes.code = normalizeCode(payload.code);
      if (!CODE_PATTERN.test(changes.code)) return failure('INVALID_INPUT', '设备编号需为 2 至 20 位字母、数字或连字符。');
    }
    if (isProvided(payload.name)) {
      changes.name = boundedText(payload.name, 2, 40);
      if (changes.name === null) return failure('INVALID_INPUT', '设备名称需为 2 至 40 字。');
    }
    if (isProvided(payload.model)) {
      changes.model = boundedText(payload.model, 0, 40);
      if (changes.model === null) return failure('INVALID_INPUT', '设备型号不超过 40 字。');
    }
    if (isProvided(payload.labId)) {
      changes.labId = String(payload.labId || '');
      if (!changes.labId) return failure('INVALID_INPUT', '请选择设备所属的实验室。');
    }
    if (!Object.keys(changes).length) return failure('INVALID_INPUT', '请至少填写一个要修改的字段。');
    return runCommand({
      type: 'device.update', requestId, normalized: { deviceId, ...changes },
      apply: snapshot => {
        const device = snapshot.devices.find(item => item.id === deviceId);
        if (!device) return failure('DEVICE_NOT_FOUND', '设备不存在或演示数据已恢复。');
        if (device.status !== 'ACTIVE') return failure('DEVICE_INACTIVE', '设备已停用，不能再编辑；如需修改请先恢复启用。');
        if (changes.code && codeTaken(snapshot.devices, changes.code, deviceId)) {
          return failure('CODE_DUPLICATE', `设备编号 ${changes.code} 已存在。`);
        }
        if (changes.labId) {
          const lab = snapshot.labs.find(item => item.id === changes.labId);
          if (!lab) return failure('LAB_NOT_FOUND', '目标实验室不存在或演示数据已恢复。');
          if (lab.status !== 'ACTIVE') return failure('LAB_INACTIVE', '目标实验室已停用，不能迁入设备。');
        }
        Object.assign(device, changes);
        return success({ device: deviceView(device, snapshot) });
      }
    });
  }

  /** device.disable — retires the device and cancels the inspections still planned for it. */
  function disableDevice(payload, requestId) {
    const deviceId = String(payload.deviceId || '');
    if (!deviceId) return failure('INVALID_INPUT', '请指定要停用的设备。');
    return runCommand({
      type: 'device.disable', requestId, normalized: { deviceId },
      apply: snapshot => {
        const device = snapshot.devices.find(item => item.id === deviceId);
        if (!device) return failure('DEVICE_NOT_FOUND', '设备不存在或演示数据已恢复。');
        if (device.status !== 'ACTIVE') return failure('INVALID_TRANSITION', '设备已处于停用状态。');
        device.status = 'INACTIVE';
        const planned = snapshot.inspections.filter(item => item.deviceId === deviceId && item.status === 'PLANNED');
        planned.forEach(item => { item.status = 'CANCELLED'; });
        return success({ device: deviceView(device, snapshot), cancelledInspectionCount: planned.length });
      }
    });
  }

  /** device.enable — returns a disabled device to the editable, reportable ledger. */
  function enableDevice(payload, requestId) {
    const deviceId = String(payload.deviceId || '');
    if (!deviceId) return failure('INVALID_INPUT', '请指定要启用的设备。');
    return runCommand({
      type: 'device.enable', requestId, normalized: { deviceId },
      apply: snapshot => {
        const device = snapshot.devices.find(item => item.id === deviceId);
        if (!device) return failure('DEVICE_NOT_FOUND', '设备不存在或演示数据已恢复。');
        if (device.status !== 'INACTIVE') return failure('INVALID_TRANSITION', '设备已处于启用状态。');
        device.status = 'ACTIVE';
        return success({ device: deviceView(device, snapshot) });
      }
    });
  }

  /** inspection.submit — records one result per template item plus its own event. */
  function submitInspection(payload, requestId) {
    const templateId = String(payload.templateId || '');
    const deviceId = String(payload.deviceId || '');
    if (!templateId || !deviceId) return failure('INVALID_INPUT', '请选择巡检模板和设备。');
    if (!Array.isArray(payload.results)) return failure('INVALID_INPUT', '巡检结果必须是逐项填写的数组。');
    const results = [];
    for (const entry of payload.results) {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        return failure('INVALID_INPUT', '每条巡检结果必须是包含检查项与结论的对象。');
      }
      const item = boundedText(entry.item, 1, 100);
      const note = isProvided(entry.note) ? boundedText(entry.note, 0, 500) : '';
      if (item === null || note === null || typeof entry.passed !== 'boolean') {
        return failure('INVALID_INPUT', '每条巡检结果需要检查项、是否合格的布尔结论，备注不超过 500 字。');
      }
      results.push({ item, passed: entry.passed, note });
    }
    return runCommand({
      type: 'inspection.submit', requestId, normalized: { templateId, deviceId, results },
      apply: snapshot => {
        const template = templatesOf(snapshot).find(item => item.id === templateId);
        if (!template) return failure('TEMPLATE_NOT_FOUND', '巡检模板不存在或演示数据已恢复。');
        const device = snapshot.devices.find(item => item.id === deviceId);
        if (!device) return failure('DEVICE_NOT_FOUND', '设备不存在或演示数据已恢复。');
        if (device.status !== 'ACTIVE') return failure('DEVICE_INACTIVE', '设备已停用，不能提交巡检。');
        if (template.deviceId !== deviceId) return failure('TEMPLATE_DEVICE_MISMATCH', '巡检模板不属于所选设备。');
        const items = Array.isArray(template.items) ? template.items : [];
        if (results.length !== items.length) {
          return failure('INVALID_INPUT', `巡检结果需要覆盖模板的全部 ${items.length} 个检查项，当前提交 ${results.length} 项。`);
        }
        const now = clock();
        const inspection = {
          id: `insp-${createId()}`, templateId, deviceId, title: template.title,
          status: 'SUBMITTED', submittedAt: now, results, isDemo: true
        };
        const event = {
          id: `evt-${createId()}`, entityType: 'Inspection', entityId: inspection.id,
          action: 'SUBMIT', from: null, to: 'SUBMITTED', actorRole: snapshot.demoRole,
          reason: '提交巡检', at: now
        };
        if (snapshot.inspections.some(item => item.id === inspection.id) || snapshot.events.some(item => item.id === event.id)) {
          return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        }
        snapshot.inspections.push(inspection);
        snapshot.events.push(event);
        return success({ inspection: inspectionView(inspection, snapshot), event });
      }
    });
  }

  /**
   * document.import — creates a new fictional document with one DRAFT version;
   * draft content is only searchable after an explicit publish.
   */
  function importDocument(payload, requestId) {
    const title = boundedText(payload.title, 2, 80);
    const sections = parseSections(payload.sections);
    if (title === null || sections === null) {
      return failure('INVALID_INPUT', '请填写 2 至 80 字标题，并至少提供一个章节（章节名 1 至 60 字，正文 1 至 2000 字）。');
    }
    const expiry = parseExpiry(payload.effectiveUntil);
    if (!expiry) return failure('INVALID_INPUT', '生效期限必须是合法的日期时间。');
    return runCommand({
      type: 'document.import', requestId, normalized: { title, sections, effectiveUntil: expiry.expiry },
      apply: snapshot => {
        const document = { id: `doc-${createId()}`, title, currentVersionId: null, isDemo: true };
        const version = { id: `docv-${createId()}`, documentId: document.id, version: 'v1', status: 'DRAFT', effectiveUntil: expiry.expiry };
        const chunks = sections.map(section => ({ id: `chunk-${createId()}`, documentVersionId: version.id, section: section.section, text: section.text }));
        const taken = [document.id, version.id, ...chunks.map(chunk => chunk.id)];
        if (taken.some(id => snapshot.documents.some(item => item.id === id)
          || snapshot.documentVersions.some(item => item.id === id)
          || snapshot.documentChunks.some(item => item.id === id))) {
          return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        }
        snapshot.documents.push(document);
        snapshot.documentVersions.push(version);
        snapshot.documentChunks.push(...chunks);
        return success({ document: { ...document }, version: { ...version }, chunkCount: chunks.length });
      }
    });
  }

  /**
   * document.revise — adds a new DRAFT version for an existing document;
   * published versions are immutable and stay retrievable for old citations.
   */
  function reviseDocument(payload, requestId) {
    const documentId = String(payload.documentId || '');
    const sections = parseSections(payload.sections);
    if (!documentId || sections === null) {
      return failure('INVALID_INPUT', '请指定要修订的资料，并至少提供一个章节（章节名 1 至 60 字，正文 1 至 2000 字）。');
    }
    const expiry = parseExpiry(payload.effectiveUntil);
    if (!expiry) return failure('INVALID_INPUT', '生效期限必须是合法的日期时间。');
    return runCommand({
      type: 'document.revise', requestId, normalized: { documentId, sections, effectiveUntil: expiry.expiry },
      apply: snapshot => {
        const document = snapshot.documents.find(item => item.id === documentId);
        if (!document) return failure('DOCUMENT_NOT_FOUND', '资料不存在或演示数据已恢复。');
        if (snapshot.documentVersions.some(item => item.documentId === documentId && item.status === 'DRAFT')) {
          return failure('DRAFT_EXISTS', '该资料已有待发布草稿，请先发布或重置演示数据。');
        }
        const version = { id: `docv-${createId()}`, documentId, version: nextVersionLabel(snapshot, documentId), status: 'DRAFT', effectiveUntil: expiry.expiry };
        const chunks = sections.map(section => ({ id: `chunk-${createId()}`, documentVersionId: version.id, section: section.section, text: section.text }));
        const taken = [version.id, ...chunks.map(chunk => chunk.id)];
        if (taken.some(id => snapshot.documentVersions.some(item => item.id === id) || snapshot.documentChunks.some(item => item.id === id))) {
          return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        }
        snapshot.documentVersions.push(version);
        snapshot.documentChunks.push(...chunks);
        return success({ document: { ...document }, version: { ...version }, chunkCount: chunks.length });
      }
    });
  }

  /** document.publish — the DRAFT becomes CURRENT; the old CURRENT is retained as SUPERSEDED. */
  function publishDocument(payload, requestId) {
    const documentId = String(payload.documentId || '');
    if (!documentId) return failure('INVALID_INPUT', '请指定要发布的资料。');
    return runCommand({
      type: 'document.publish', requestId, normalized: { documentId },
      apply: snapshot => {
        const document = snapshot.documents.find(item => item.id === documentId);
        if (!document) return failure('DOCUMENT_NOT_FOUND', '资料不存在或演示数据已恢复。');
        const draft = snapshot.documentVersions.find(item => item.documentId === documentId && item.status === 'DRAFT');
        if (!draft) return failure('NO_DRAFT', '该资料没有待发布的草稿版本。');
        snapshot.documentVersions.forEach(version => {
          if (version.documentId === documentId && version.status === 'CURRENT') version.status = 'SUPERSEDED';
        });
        draft.status = 'CURRENT';
        document.currentVersionId = draft.id;
        return success({ document: { ...document }, version: { ...draft } });
      }
    });
  }

  /**
   * attachment.record — stores image file METADATA only (name, size, local path);
   * the binary never enters the snapshot, so exports stay small and portable.
   */
  function recordAttachment(payload, requestId) {
    const entityType = String(payload.entityType || '');
    const entityId = String(payload.entityId || '');
    const fileName = boundedText(payload.fileName, 1, 100);
    const localPath = isProvided(payload.localPath) ? boundedText(payload.localPath, 0, 200) : '';
    const size = payload.size;
    if (entityType !== 'Incident' || !entityId || fileName === null || localPath === null
      || !Number.isSafeInteger(size) || size < 0 || size > ATTACHMENT_MAX_BYTES) {
      return failure('INVALID_INPUT', '请提供附件所属的演示记录、1 至 100 字文件名和不超过 10 MB 的文件大小。');
    }
    return runCommand({
      type: 'attachment.record', requestId, normalized: { entityType, entityId, fileName, size, localPath },
      apply: snapshot => {
        const incident = snapshot.incidents.find(item => item.id === entityId);
        if (!incident) return failure('ENTITY_NOT_FOUND', '附件关联的异常记录不存在或演示数据已恢复。');
        const attachment = {
          id: `att-${createId()}`, entityType, entityId, fileName, size,
          localPath: localPath || null, recordedAt: clock(), isDemo: true
        };
        if (attachmentsOf(snapshot).some(item => item.id === attachment.id)) {
          return failure('ID_COLLISION', '生成的记录编号冲突，请重试。');
        }
        snapshot.attachments.push(attachment);
        return success({ attachment: { ...attachment } });
      }
    });
  }

  /** Read-only, stable view contracts; never leaks mutable snapshot references. */
  async function query({ type, payload } = {}) {
    const input = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
    const { snapshot } = await read();
    switch (type) {
      case 'dashboard.summary':
        return {
          deviceCount: snapshot.devices.length,
          inspectionCount: snapshot.inspections.length,
          incidentCount: snapshot.incidents.length,
          openIncidentCount: snapshot.incidents.filter(item => !['CLOSED', 'REJECTED'].includes(item.status)).length
        };
      case 'lab.list':
        return { items: snapshot.labs.map(lab => labView(lab, snapshot)) };
      case 'lab.detail': {
        const lab = snapshot.labs.find(item => item.id === input.labId);
        if (!lab) return { lab: null, devices: [] };
        return {
          lab: labView(lab, snapshot),
          devices: snapshot.devices.filter(item => item.labId === lab.id).map(device => deviceView(device, snapshot))
        };
      }
      case 'device.list':
        return { items: filterItems(snapshot.devices, input.filter, DEVICE_FILTERS).map(device => deviceView(device, snapshot)) };
      case 'device.detail': {
        const device = snapshot.devices.find(item => item.id === input.deviceId);
        return { device: device ? deviceView(device, snapshot) : null };
      }
      case 'inspection.template.list':
        return {
          items: templatesOf(snapshot).map(template => ({
            ...template,
            items: Array.isArray(template.items) ? template.items.slice() : []
          }))
        };
      case 'inspection.list':
        return { items: filterItems(snapshot.inspections, input.filter, INSPECTION_FILTERS).map(item => inspectionView(item, snapshot)) };
      case 'inspection.detail': {
        const inspection = snapshot.inspections.find(item => item.id === input.inspectionId);
        if (!inspection) return { inspection: null, template: null, events: [] };
        const template = templatesOf(snapshot).find(item => item.id === inspection.templateId) || null;
        return {
          inspection: inspectionView(inspection, snapshot),
          template,
          events: entityEvents(snapshot, 'Inspection', inspection.id)
        };
      }
      case 'incident.list':
        return { items: filterItems(snapshot.incidents, input.filter, INCIDENT_FILTERS).map(item => incidentView(item, snapshot)) };
      case 'incident.detail': {
        const incident = snapshot.incidents.find(item => item.id === input.incidentId);
        if (!incident) return { incident: null, events: [], attachments: [] };
        return {
          incident: incidentView(incident, snapshot),
          events: entityEvents(snapshot, 'Incident', incident.id),
          attachments: entityAttachments(snapshot, 'Incident', incident.id)
        };
      }
      case 'demo.session':
        return { role: snapshot.demoRole, label: ROLES[snapshot.demoRole] || '未知角色', isDemo: true };
      case 'knowledge.document.list':
        return {
          items: snapshot.documents.map(document => {
            const current = snapshot.documentVersions.find(item => item.id === document.currentVersionId) || null;
            const hasDraft = snapshot.documentVersions.some(item => item.documentId === document.id && item.status === 'DRAFT');
            return {
              id: document.id,
              title: document.title,
              currentVersion: current ? current.version : null,
              effectiveUntil: current ? (current.effectiveUntil || null) : null,
              hasDraft
            };
          })
        };
      case 'knowledge.search': {
        const text = String(input.text || '').trim().toLowerCase();
        if (!text) return { status: 'NO_EVIDENCE', items: [] };
        const now = clock();
        const items = snapshot.documentChunks.flatMap(chunk => {
          const version = snapshot.documentVersions.find(item => item.id === chunk.documentVersionId);
          if (!versionInForce(version, now)) return [];
          const document = snapshot.documents.find(item => item.id === version.documentId);
          if (!document || !`${document.title} ${chunk.section} ${chunk.text}`.toLowerCase().includes(text)) return [];
          return [{ id: chunk.id, title: document.title, text: chunk.text,
            source: { documentTitle: document.title, version: version.version, section: chunk.section, effectiveUntil: version.effectiveUntil || null } }];
        });
        return { status: items.length ? 'FOUND' : 'NO_EVIDENCE', items };
      }
      case 'knowledge.citation': {
        const chunk = snapshot.documentChunks.find(item => item.id === String(input.citationId || input.chunkId || ''));
        if (!chunk) return { citation: null, version: null, document: null, isCurrent: false };
        const version = snapshot.documentVersions.find(item => item.id === chunk.documentVersionId) || null;
        const document = version ? snapshot.documents.find(item => item.id === version.documentId) || null : null;
        return {
          citation: { id: chunk.id, section: chunk.section, text: chunk.text },
          version: version ? { id: version.id, version: version.version, status: version.status, effectiveUntil: version.effectiveUntil || null } : null,
          document: document ? { id: document.id, title: document.title } : null,
          isCurrent: !!version && version.status === 'CURRENT'
        };
      }
      case 'assistant.parseDraft': {
        const text = String(input.text || '').trim();
        if (!text) {
          return { intent: 'UNKNOWN', draft: null, missing: [], valid: false, message: '请输入一句话描述，例如“SIM-DEV-001 通风柜风速偏低”。' };
        }
        const lowerText = text.toLowerCase();
        const device = snapshot.devices.find(item => lowerText.includes(item.code.toLowerCase())
          || text.includes(item.name.replace(/^模拟/, ''))) || null;
        if (text.includes('巡检')) {
          const template = (device && snapshot.inspectionTemplates.find(item => item.deviceId === device.id))
            || snapshot.inspectionTemplates[0] || null;
          const missing = [];
          if (!device) missing.push('设备（可写设备编号，如 SIM-DEV-001）');
          if (!template) missing.push('巡检模板');
          return {
            intent: 'INSPECTION',
            draft: {
              deviceId: device ? device.id : null,
              deviceName: device ? device.name : null,
              templateId: template ? template.id : null,
              templateTitle: template ? template.title : null,
              results: template && Array.isArray(template.items)
                ? template.items.map(item => ({ item, passed: true, note: '' }))
                : []
            },
            missing,
            valid: missing.length === 0
          };
        }
        if (INCIDENT_HINTS.some(hint => text.includes(hint))) {
          const sentences = text.split(/[。！？!?;；\n]/).map(part => part.trim()).filter(Boolean);
          const titleSource = (device && sentences.find(sentence => !sentence.toLowerCase().includes(device.code.toLowerCase()))) || sentences[0] || '';
          const title = titleSource.slice(0, 80);
          const severity = /紧急|严重/.test(text) ? 'HIGH' : /中等|较重/.test(text) ? 'MEDIUM' : 'LOW';
          const missing = [];
          if (!device) missing.push('设备（可写设备编号，如 SIM-DEV-001）');
          if (title.length < 2) missing.push('异常标题（至少 2 字）');
          return {
            intent: 'INCIDENT',
            draft: {
              deviceId: device ? device.id : null,
              deviceName: device ? device.name : null,
              title,
              description: text.slice(0, 1000),
              severity
            },
            missing,
            valid: missing.length === 0
          };
        }
        return {
          intent: 'UNKNOWN', draft: null, missing: [], valid: false,
          message: '尚未识别出异常上报或巡检意图；可在描述中包含“异常”或“巡检”字样后重试。'
        };
      }
      default:
        throw domainError('UNKNOWN_QUERY', `不支持的查询：${type || '空'}`);
    }
  }

  /** Mutations return Result and commit the whole aggregate with optimistic generation. */
  async function execute({ type, payload, requestId } = {}) {
    const input = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
    try {
      if (type === 'demo.role.switch') {
        const role = String(input.role || '');
        if (!Object.prototype.hasOwnProperty.call(ROLES, role)) return failure('INVALID_ROLE', '演示角色无效。');
        const { generation, snapshot } = await read();
        snapshot.demoRole = role;
        await repository.commit(generation, snapshot);
        return success({ role, label: ROLES[role], isDemo: true });
      }
      if (type === 'incident.report') return await reportIncident(input, requestId);
      if (Object.prototype.hasOwnProperty.call(INCIDENT_TRANSITIONS, type)) {
        return await transitionIncident(type, input, requestId);
      }
      if (type === 'lab.create') return await createLab(input, requestId);
      if (type === 'lab.update') return await updateLab(input, requestId);
      if (type === 'device.create') return await createDevice(input, requestId);
      if (type === 'device.update') return await updateDevice(input, requestId);
      if (type === 'device.disable') return await disableDevice(input, requestId);
      if (type === 'device.enable') return await enableDevice(input, requestId);
      if (type === 'inspection.submit') return await submitInspection(input, requestId);
      if (type === 'document.import') return await importDocument(input, requestId);
      if (type === 'document.revise') return await reviseDocument(input, requestId);
      if (type === 'document.publish') return await publishDocument(input, requestId);
      if (type === 'attachment.record') return await recordAttachment(input, requestId);
      return failure('UNKNOWN_COMMAND', `不支持的操作：${type || '空'}`);
    } catch (error) {
      return failure(error.code || 'STORAGE_ERROR', error.message || '本机数据操作失败。');
    }
  }

  /** Backup envelope with a content checksum; the snapshot itself never carries binaries. */
  function backupFrom(snapshot) {
    return {
      kind: BACKUP_KIND,
      backupVersion: BACKUP_VERSION,
      schemaVersion: snapshot.schemaVersion,
      exportedAt: clock(),
      snapshot,
      checksum: sha256(canonicalStringify(snapshot))
    };
  }

  /** Full validation before any use: structure, kind, schema, then checksum. */
  function validateBackup(backup) {
    if (!backup || typeof backup !== 'object' || Array.isArray(backup)) {
      return failure('BACKUP_INVALID', '备份内容不是有效的 JSON 对象。');
    }
    if (backup.kind !== BACKUP_KIND) return failure('BACKUP_INVALID', '这不是本演示程序导出的备份。');
    if (backup.backupVersion !== BACKUP_VERSION) return failure('BACKUP_UNSUPPORTED', '备份格式版本不受当前程序支持。');
    const snapshot = backup.snapshot;
    if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
      return failure('BACKUP_INVALID', '备份缺少快照数据。');
    }
    if (snapshot.schemaVersion !== SCHEMA_VERSION && snapshot.schemaVersion !== 1) {
      return failure('BACKUP_UNSUPPORTED', `备份 schema 版本 ${snapshot.schemaVersion} 不受当前程序支持。`);
    }
    if (typeof backup.checksum !== 'string' || !/^[a-f0-9]{64}$/.test(backup.checksum)) {
      return failure('BACKUP_INVALID', '备份校验值缺失或格式不正确。');
    }
    let canonical;
    try { canonical = canonicalStringify(snapshot); }
    catch (cause) { return failure('BACKUP_INVALID', '备份快照包含不支持的数据。'); }
    if (sha256(canonical) !== backup.checksum) {
      return failure('BACKUP_CHECKSUM_MISMATCH', '备份校验值不匹配，内容可能已损坏，已拒绝恢复。');
    }
    return success({ snapshot });
  }

  /** Capacity and portability warnings for a restored/exported snapshot. */
  function backupWarnings(snapshot) {
    const byteSize = utf8ByteLength(canonicalStringify(snapshot));
    const warnings = [];
    if (byteSize > BACKUP_WARN_BYTES) {
      warnings.push(`备份数据约 ${Math.ceil(byteSize / 1024)} KB，已超过安全余量，接近微信单键 1 MB 限制，建议减少演示数据或先重置。`);
    }
    const attachmentCount = Array.isArray(snapshot.attachments) ? snapshot.attachments.length : 0;
    if (attachmentCount > 0) {
      warnings.push(`备份包含 ${attachmentCount} 条图片附件记录（仅元数据，不含图片本身）；异机恢复后附件文件会缺失，需重新选择图片。`);
    }
    return { byteSize, warnings };
  }

  async function maintenance({ type, payload } = {}) {
    try {
      if (type === 'demo.reset') {
        const { generation } = await read();
        await repository.commit(generation, createSeedSnapshot());
        return success({ reset: true, isDemo: true });
      }
      if (type === 'backup.export') {
        const { snapshot } = await read();
        const { byteSize, warnings } = backupWarnings(snapshot);
        return success({ backup: backupFrom(snapshot), byteSize, warnings });
      }
      if (type === 'backup.precheck' || type === 'backup.restore') {
        // 全部校验通过之前不触碰当前数据；校验失败绝不部分覆盖。
        const checked = validateBackup(payload && payload.backup);
        if (!checked.ok) return checked;
        const clone = JSON.parse(canonicalStringify(checked.data.snapshot));
        // v1 先按明确规则迁移，再对目标 v2 做深度校验与容量判定。
        const migration = migrateSnapshot(clone);
        const defaultsApplied = applyLegacyDefaults(migration.snapshot);
        const invariants = validateSnapshotInvariants(migration.snapshot);
        if (!invariants.ok) return invariants;
        const { generation } = await read();
        // P0-3：按将要写入的完整槽记录（generation + checksum + snapshot）计算字节数，
        // 与 WxLocalRepository.commit 同口径；超限在预检阶段即拒绝，不再出现
        // “预检可恢复、恢复必报 STORAGE_LIMIT”。
        const writeBytes = slotRecordBytes(generation + 1, migration.snapshot);
        if (writeBytes > MAX_RECORD_BYTES) {
          return failure('STORAGE_LIMIT', `恢复后的完整数据约 ${Math.ceil(writeBytes / 1024)} KB，超过本机单键安全余量 ${Math.ceil(MAX_RECORD_BYTES / 1024)} KB，已拒绝恢复。`);
        }
        const { byteSize, warnings } = backupWarnings(migration.snapshot);
        const report = {
          schemaVersion: migration.snapshot.schemaVersion,
          migrated: migration.migrated || defaultsApplied,
          byteSize,
          writeBytes,
          warnings,
          counts: {
            labs: migration.snapshot.labs.length,
            devices: migration.snapshot.devices.length,
            inspections: migration.snapshot.inspections.length,
            incidents: migration.snapshot.incidents.length,
            events: migration.snapshot.events.length,
            documents: migration.snapshot.documents.length,
            attachments: attachmentsOf(migration.snapshot).length
          }
        };
        if (type === 'backup.precheck') return success({ canRestore: true, ...report });
        await repository.commit(generation, migration.snapshot);
        return success({ restored: true, ...report });
      }
      return failure('UNKNOWN_MAINTENANCE', `不支持的维护操作：${type || '空'}`);
    } catch (error) {
      return failure(error.code || 'STORAGE_ERROR', error.message || '维护操作失败。');
    }
  }

  return Object.freeze({ query, execute, maintenance });
}

module.exports = { createLabSafeCore, INCIDENT_TRANSITIONS, migrateSnapshot, BACKUP_KIND, BACKUP_VERSION };
