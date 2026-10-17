const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore, migrateSnapshot, BACKUP_KIND, BACKUP_VERSION } = require('../miniprogram/core/createLabSafeCore');
const { canonicalStringify, sha256 } = require('../miniprogram/core/hash');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { WxLocalRepository, KEYS, MAX_RECORD_BYTES, slotRecordBytes } = require('../miniprogram/infrastructure/WxLocalRepository');
const { unwrap } = require('../miniprogram/pages/_shared/ui');

function fakeStorage() {
  const values = new Map();
  return {
    values,
    getStorageSync(key) { return values.has(key) ? values.get(key) : ''; },
    setStorageSync(key, value) { values.set(key, value); }
  };
}

function makeCore(options = {}) {
  let counter = 0;
  const repository = options.repository || new MemoryRepository({ seedFactory: createSeedSnapshot });
  return createLabSafeCore({
    repository,
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `p0-${++counter}`
  });
}

/** Build a realistic v1 slot record: current seed minus every v2-only collection. */
function v1Snapshot() {
  const snapshot = JSON.parse(canonicalStringify(createSeedSnapshot()));
  snapshot.schemaVersion = 1;
  delete snapshot.inspectionTemplates;
  delete snapshot.documents;
  delete snapshot.documentVersions;
  delete snapshot.documentChunks;
  delete snapshot.attachments;
  return snapshot;
}

function v1SlotRecord(generation, snapshot = v1Snapshot()) {
  const canonical = canonicalStringify(snapshot);
  return { generation, checksum: sha256(canonical), snapshot: JSON.parse(canonical) };
}

function backupOf(snapshot) {
  return {
    kind: BACKUP_KIND,
    backupVersion: BACKUP_VERSION,
    schemaVersion: snapshot.schemaVersion,
    exportedAt: '2026-10-16T10:00:00+08:00',
    snapshot,
    checksum: sha256(canonicalStringify(snapshot))
  };
}

test('P0-1: a committed v1 slot loads through the real repository, one write upgrades it to v2', async () => {
  const storage = fakeStorage();
  const record = v1SlotRecord(7);
  storage.values.set(KEYS.A, JSON.stringify(record));
  storage.values.set(KEYS.pointer, JSON.stringify({ slot: 'A', generation: 7, checksum: record.checksum }));

  const core = makeCore({ repository: new WxLocalRepository({ storage, seedFactory: createSeedSnapshot }) });
  // 启动即可读：v1 本机数据不丢，原集合仍然可见
  const summary = unwrap(await core.query({ type: 'dashboard.summary' }));
  assert.deepEqual(summary, { deviceCount: 4, inspectionCount: 2, incidentCount: 2, openIncidentCount: 2 });

  // 一次正常写入
  const reported = unwrap(await core.execute({
    type: 'incident.report', requestId: 'p0-1-write',
    payload: { deviceId: 'dev-demo-03', title: 'v1 升级写入', description: '写入后应为 v2', severity: 'LOW' }
  }));

  // 重启（全新仓库实例）后数据与代次延续
  const restarted = makeCore({ repository: new WxLocalRepository({ storage, seedFactory: createSeedSnapshot }) });
  const after = unwrap(await restarted.query({ type: 'dashboard.summary' }));
  assert.equal(after.incidentCount, 3);
  const detail = unwrap(await restarted.query({ type: 'incident.detail', payload: { incidentId: reported.incident.id } }));
  assert.equal(detail.incident.title, 'v1 升级写入');

  // 写入后的活跃槽记录已是 v2，且指针指向新槽
  const pointer = JSON.parse(storage.values.get(KEYS.pointer));
  assert.equal(pointer.generation, 8);
  const written = JSON.parse(storage.values.get(pointer.slot === 'A' ? KEYS.A : KEYS.B));
  assert.equal(written.snapshot.schemaVersion, 2);
  assert.equal(written.generation, 8);
});

test('P0-1: corrupt or ambiguous v1 storage still follows the conservative strategy', async () => {
  // 坏校验值：指针指向被篡改的 v1 槽 → 显式拒绝，不静默重置
  const tampered = fakeStorage();
  const record = v1SlotRecord(3);
  record.checksum = '0'.repeat(64);
  tampered.values.set(KEYS.A, JSON.stringify(record));
  tampered.values.set(KEYS.pointer, JSON.stringify({ slot: 'A', generation: 3, checksum: record.checksum }));
  await assert.rejects(
    new WxLocalRepository({ storage: tampered }).load(),
    error => error.code === 'STORAGE_CORRUPT'
  );
  assert.equal(tampered.values.size, 2);

  // 单个 v1 槽但没有可信指针 → 无法区分已提交与未提交，显式 STORAGE_UNCERTAIN
  const ambiguous = fakeStorage();
  const alone = v1SlotRecord(2);
  ambiguous.values.set(KEYS.B, JSON.stringify(alone));
  await assert.rejects(
    new WxLocalRepository({ storage: ambiguous }).load(),
    error => error.code === 'STORAGE_UNCERTAIN'
  );

  // 有效 v1 指针 + 另一个未提交的较新坏槽 → 仍读取指针指定的 v1
  const prepared = fakeStorage();
  const committed = v1SlotRecord(5);
  prepared.values.set(KEYS.A, JSON.stringify(committed));
  prepared.values.set(KEYS.pointer, JSON.stringify({ slot: 'A', generation: 5, checksum: committed.checksum }));
  prepared.values.set(KEYS.B, JSON.stringify({ generation: 6, checksum: 'bad' }));
  const repository = new WxLocalRepository({ storage: prepared });
  const state = await repository.load();
  assert.equal(state.generation, 5);
  assert.equal(state.snapshot.schemaVersion, 1);
});

test('P0-2: backups with broken references are rejected by the same deep validator in precheck and restore', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });
  unwrap(await core.execute({
    type: 'incident.report', requestId: 'p0-2-new',
    payload: { deviceId: 'dev-demo-01', title: '恢复前新增', description: '校验失败后必须原样保留', severity: 'LOW' }
  }));
  const before = unwrap(await core.query({ type: 'dashboard.summary' }));
  const generationBefore = repository.generation;

  // 反例 1：删除被异常/巡检/模板引用的设备后重算校验值
  const broken = JSON.parse(canonicalStringify(createSeedSnapshot()));
  broken.devices = broken.devices.filter(device => device.id !== 'dev-demo-01');
  const brokenBackup = backupOf(broken);
  const brokenPrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: brokenBackup } });
  assert.equal(brokenPrecheck.ok, false, '断裂设备引用不应通过预检');
  const brokenRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: brokenBackup } });
  assert.equal(brokenRestore.ok, false);

  // 反例 2：重复 ID
  const duplicated = JSON.parse(canonicalStringify(createSeedSnapshot()));
  duplicated.devices.push({ ...duplicated.devices[0] });
  const duplicateRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: backupOf(duplicated) } });
  assert.equal(duplicateRestore.ok, false);

  // 反例 3：文档指向不存在的版本
  const dangling = JSON.parse(canonicalStringify(createSeedSnapshot()));
  dangling.documents[0].currentVersionId = 'docv-missing';
  const danglingPrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: backupOf(dangling) } });
  assert.equal(danglingPrecheck.ok, false);

  // 反例 4：非法异常状态
  const illegal = JSON.parse(canonicalStringify(createSeedSnapshot()));
  illegal.incidents[0].status = 'FOO';
  const illegalRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: backupOf(illegal) } });
  assert.equal(illegalRestore.ok, false);

  // v1 备份同样先迁移再校验：v1 中断裂的设备引用必须被拒绝
  const legacyBroken = v1Snapshot();
  legacyBroken.devices = legacyBroken.devices.filter(device => device.id !== 'dev-demo-02');
  const legacyBackup = backupOf(legacyBroken);
  const legacyPrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: legacyBackup } });
  assert.equal(legacyPrecheck.ok, false);

  // 全部拒绝后：当前快照、代次不变
  assert.deepEqual(unwrap(await core.query({ type: 'dashboard.summary' })), before);
  assert.equal(repository.generation, generationBefore);
});

test('P0-2: records missing business-required fields are rejected by precheck and restore', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });
  unwrap(await core.execute({
    type: 'incident.report', requestId: 'p0-2b-new',
    payload: { deviceId: 'dev-demo-01', title: '必需字段基线', description: '缺字段备份必须被拒绝', severity: 'LOW' }
  }));
  const before = unwrap(await core.query({ type: 'dashboard.summary' }));
  const generationBefore = repository.generation;

  // 反例 1：设备缺 code（助手解析与页面投影都按 code 读取）
  const noCode = JSON.parse(canonicalStringify(createSeedSnapshot()));
  delete noCode.devices[0].code;
  const noCodeBackup = backupOf(noCode);
  const noCodePrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: noCodeBackup } });
  assert.equal(noCodePrecheck.ok, false, '缺少设备编号的备份不应通过预检');
  assert.equal(noCodePrecheck.error.code, 'BACKUP_INVALID');
  const noCodeRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: noCodeBackup } });
  assert.equal(noCodeRestore.ok, false);

  // 反例 2：事件缺 at（时间线按 at 排序）
  const noAt = JSON.parse(canonicalStringify(createSeedSnapshot()));
  delete noAt.events[0].at;
  const noAtBackup = backupOf(noAt);
  const noAtPrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: noAtBackup } });
  assert.equal(noAtPrecheck.ok, false, '缺少事件时间的备份不应通过预检');
  assert.equal(noAtPrecheck.error.code, 'BACKUP_INVALID');
  const noAtRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: noAtBackup } });
  assert.equal(noAtRestore.ok, false);

  // 反例 3：字段类型错误（异常 revision 不是数字，乐观并发检查会失效）
  const badRevision = JSON.parse(canonicalStringify(createSeedSnapshot()));
  badRevision.incidents[0].revision = '1';
  const badRevisionRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: backupOf(badRevision) } });
  assert.equal(badRevisionRestore.ok, false);

  // 全部拒绝后：当前快照、代次不变
  assert.deepEqual(unwrap(await core.query({ type: 'dashboard.summary' })), before);
  assert.equal(repository.generation, generationBefore);

  // 真实仓库：拒绝后落盘字节与指针不变
  const storage = fakeStorage();
  const realCore = makeCore({ repository: new WxLocalRepository({ storage, seedFactory: createSeedSnapshot }) });
  const rawBefore = JSON.stringify({
    a: storage.values.get(KEYS.A), b: storage.values.get(KEYS.B), p: storage.values.get(KEYS.pointer)
  });
  const rejected = await realCore.maintenance({ type: 'backup.restore', payload: { backup: noCodeBackup } });
  assert.equal(rejected.ok, false);
  assert.equal(JSON.stringify({
    a: storage.values.get(KEYS.A), b: storage.values.get(KEYS.B), p: storage.values.get(KEYS.pointer)
  }), rawBefore);
});

test('P0-2: malformed knowledge expiry is rejected before restoring', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });
  const snapshot = JSON.parse(canonicalStringify(createSeedSnapshot()));
  snapshot.documentVersions[0].effectiveUntil = 20270101;
  const backup = backupOf(snapshot);

  const precheck = await core.maintenance({ type: 'backup.precheck', payload: { backup } });
  assert.equal(precheck.ok, false, '资料页需要可读取的时间字符串，数字有效期不能进入备份');
  const restore = await core.maintenance({ type: 'backup.restore', payload: { backup } });
  assert.equal(restore.ok, false);
  assert.equal(repository.generation, 0);
});

test('P0-2: legacy snapshots missing role or receipts export and restore with safe defaults', async () => {
  for (const missingField of ['demoRole', 'receipts']) {
    const seedFactory = () => {
      const snapshot = createSeedSnapshot();
      delete snapshot[missingField];
      return snapshot;
    };
    const repository = new MemoryRepository({ seedFactory });
    const core = makeCore({ repository });
    const exported = unwrap(await core.maintenance({ type: 'backup.export' })).backup;
    assert.equal(exported.snapshot.demoRole, 'student', `${missingField} 旧快照应补默认学生角色`);
    assert.deepEqual(exported.snapshot.receipts, {}, `${missingField} 旧快照应补空回执`);
    const precheck = unwrap(await core.maintenance({ type: 'backup.precheck', payload: { backup: exported } }));
    assert.equal(precheck.canRestore, true, `${missingField} 的导出备份应能预检`);
    unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup: exported } }));
    assert.equal(repository.generation, 1, `${missingField} 的导出备份应能恢复`);
  }
});

test('P0-2: malformed fields across restored entities are refused without overwriting data', async () => {
  const cases = [
    ['显式非法演示角色', snapshot => { snapshot.demoRole = null; }],
    ['显式非法回执集合', snapshot => { snapshot.receipts = null; }],
    ['实验室编号', snapshot => { delete snapshot.labs[0].code; }],
    ['实验室名称', snapshot => { snapshot.labs[0].name = 123; }],
    ['实验室状态', snapshot => { snapshot.labs[0].status = 'BROKEN'; }],
    ['实验室编号重复', snapshot => { snapshot.labs[1].code = snapshot.labs[0].code; }],
    ['实验室地址类型', snapshot => { snapshot.labs[0].location = {}; }],
    ['设备状态', snapshot => { snapshot.devices[0].status = 'BROKEN'; }],
    ['设备编号重复', snapshot => { snapshot.devices[1].code = snapshot.devices[0].code; }],
    ['设备型号类型', snapshot => { snapshot.devices[0].model = 12; }],
    ['巡检模板标题', snapshot => { delete snapshot.inspectionTemplates[0].title; }],
    ['巡检模板检查项', snapshot => { snapshot.inspectionTemplates[0].items = [null]; }],
    ['巡检模板周期', snapshot => { snapshot.inspectionTemplates[0].intervalDays = 0; }],
    ['巡检标题', snapshot => { delete snapshot.inspections[0].title; }],
    ['巡检模板与设备不匹配', snapshot => { snapshot.inspections[0].templateId = 'tmpl-demo-02'; }],
    ['巡检到期时间类型', snapshot => { snapshot.inspections[0].dueAt = 42; }],
    ['巡检结果类型', snapshot => { snapshot.inspections[0].results = {}; }],
    ['异常编号', snapshot => { delete snapshot.incidents[0].code; }],
    ['异常标题', snapshot => { delete snapshot.incidents[0].title; }],
    ['异常描述类型', snapshot => { snapshot.incidents[0].description = 123; }],
    ['异常级别', snapshot => { snapshot.incidents[0].severity = 'BOGUS'; }],
    ['异常创建时间', snapshot => { delete snapshot.incidents[0].createdAt; }],
    ['异常更新时间', snapshot => { snapshot.incidents[0].updatedAt = 123; }],
    ['事件动作', snapshot => { delete snapshot.events[0].action; }],
    ['事件实体类型原型链键', snapshot => { snapshot.events[0].entityType = '__proto__'; }],
    ['事件来源状态类型', snapshot => { snapshot.events[0].from = {}; }],
    ['事件目标状态', snapshot => { delete snapshot.events[0].to; }],
    ['事件角色', snapshot => { snapshot.events[0].actorRole = 'manager'; }],
    ['事件说明类型', snapshot => { snapshot.events[0].reason = 123; }],
    ['资料标题', snapshot => { delete snapshot.documents[0].title; }],
    ['资料当前版本指向草稿', snapshot => { snapshot.documentVersions[0].status = 'DRAFT'; }],
    ['资料现行版本缺少指针', snapshot => { snapshot.documents[0].currentVersionId = null; }],
    ['资料出现多个草稿', snapshot => { snapshot.documentVersions.push({ id: 'docv-extra-draft', documentId: 'doc-demo-01', version: 'v2', status: 'DRAFT' }, { id: 'docv-extra-draft-2', documentId: 'doc-demo-01', version: 'v3', status: 'DRAFT' }); }],
    ['资料版本缺少正文', snapshot => { snapshot.documentChunks = []; }],
    ['资料版本标识', snapshot => { delete snapshot.documentVersions[0].version; }],
    ['资料章节正文', snapshot => { delete snapshot.documentChunks[0].text; }],
    ['附件大小', snapshot => { snapshot.attachments.push({
      id: 'att-bad-size', entityType: 'Incident', entityId: 'inc-demo-01',
      fileName: '模拟图片.jpg', size: -1, localPath: null, recordedAt: '2026-10-16T10:00:00+08:00'
    }); }],
    ['附件记录时间', snapshot => { snapshot.attachments.push({
      id: 'att-bad-at', entityType: 'Incident', entityId: 'inc-demo-01',
      fileName: '模拟图片.jpg', size: 5, localPath: null, recordedAt: 123
    }); }],
    ['附件路径类型', snapshot => { snapshot.attachments.push({
      id: 'att-bad-path', entityType: 'Incident', entityId: 'inc-demo-01',
      fileName: '模拟图片.jpg', size: 5, localPath: {}, recordedAt: '2026-10-16T10:00:00+08:00'
    }); }],
    ['幂等回执指纹', snapshot => { snapshot.receipts['req-bad'] = { fingerprint: 'bad', data: {} }; }],
    ['幂等回执数据', snapshot => { snapshot.receipts['req-bad'] = { fingerprint: 'a'.repeat(64), data: null }; }]
  ];

  for (const [label, mutate] of cases) {
    const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
    const core = makeCore({ repository });
    const snapshot = JSON.parse(canonicalStringify(createSeedSnapshot()));
    mutate(snapshot);
    const backup = backupOf(snapshot);
    const precheck = await core.maintenance({ type: 'backup.precheck', payload: { backup } });
    assert.equal(precheck.ok, false, `${label}不应通过预检`);
    assert.equal(precheck.error.code, 'BACKUP_INVALID');
    const restore = await core.maintenance({ type: 'backup.restore', payload: { backup } });
    assert.equal(restore.ok, false, `${label}不应通过恢复`);
    assert.equal(repository.generation, 0, `${label}拒绝后不得覆盖当前数据`);
  }
});

test('P0-2: inspection submission cannot create a backup-invalid template/device pair', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });
  const result = await core.execute({
    type: 'inspection.submit', requestId: 'p0-template-device',
    payload: {
      templateId: 'tmpl-demo-01', deviceId: 'dev-demo-02',
      results: [
        { item: '外观检查', passed: true },
        { item: '风速是否正常', passed: true },
        { item: '排风管道有无泄漏', passed: true }
      ]
    }
  });
  assert.equal(result.ok, false);
  assert.equal(repository.generation, 0);
});

test('P0-2: restore through the real repository leaves slots and pointer untouched on rejection', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage, seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });
  unwrap(await core.execute({
    type: 'incident.report', requestId: 'p0-2-commit',
    payload: { deviceId: 'dev-demo-04', title: '恢复基线', description: '拒绝后原槽不动', severity: 'LOW' }
  }));
  const rawBefore = JSON.stringify({
    a: storage.values.get(KEYS.A), b: storage.values.get(KEYS.B), p: storage.values.get(KEYS.pointer)
  });
  const broken = JSON.parse(canonicalStringify(createSeedSnapshot()));
  broken.devices = broken.devices.filter(device => device.id !== 'dev-demo-01');
  const rejected = await core.maintenance({ type: 'backup.restore', payload: { backup: backupOf(broken) } });
  assert.equal(rejected.ok, false);
  assert.equal(JSON.stringify({
    a: storage.values.get(KEYS.A), b: storage.values.get(KEYS.B), p: storage.values.get(KEYS.pointer)
  }), rawBefore);
});

test('P0-3: precheck refuses backups whose real slot record would exceed the repository limit', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });

  // 阈值下：略小于上限的备份可预检、可恢复
  const seed = JSON.parse(canonicalStringify(createSeedSnapshot()));
  const below = { ...seed, largeText: '' };
  // '汉' 占 3 字节；largeText 键已计入 baseBytes，3n 取整后 below 必不超限
  const baseBytes = slotRecordBytes(1, below);
  const n = Math.floor((MAX_RECORD_BYTES - baseBytes) / 3);
  assert.ok(n > 0, '测试前置：余量应足以构造边界样本');
  below.largeText = '汉'.repeat(n);
  assert.ok(slotRecordBytes(1, below) <= MAX_RECORD_BYTES, '测试前置：阈值下样本应确实可写');
  const belowBackup = backupOf(below);
  const belowPrecheck = unwrap(await core.maintenance({ type: 'backup.precheck', payload: { backup: belowBackup } }));
  assert.equal(belowPrecheck.canRestore, true);
  const restored = unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup: belowBackup } }));
  assert.equal(restored.restored, true);

  // 阈值上：再增加两个中文字符即超过单键安全余量
  const above = { ...below, largeText: '汉'.repeat(n + 2) };
  assert.ok(slotRecordBytes(1, above) > MAX_RECORD_BYTES, '测试前置：阈值上样本应确实超限');
  const aboveBackup = backupOf(above);
  const abovePrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: aboveBackup } });
  assert.equal(abovePrecheck.ok, false, '预检不得对超限备份返回 canRestore');
  const aboveRestore = await core.maintenance({ type: 'backup.restore', payload: { backup: aboveBackup } });
  assert.equal(aboveRestore.ok, false);
  assert.equal(aboveRestore.error.code, 'STORAGE_LIMIT');
});

test('P0-3: v1 migration bloat is measured on the migrated v2 record, not the raw backup', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage, seedFactory: createSeedSnapshot });
  const core = makeCore({ repository });

  const legacy = v1Snapshot();
  const migratedEmpty = migrateSnapshot(JSON.parse(canonicalStringify(legacy))).snapshot;
  const baseBytes = slotRecordBytes(1, migratedEmpty);
  // '汉' 占 3 字节；减去新增 JSON 键与安全余量后取整
  const n = Math.floor((MAX_RECORD_BYTES - baseBytes - 20) / 3);
  assert.ok(n > 0, '测试前置：余量应足以构造边界样本');

  // 阈值下：v1 备份迁移后仍可写
  const belowLegacy = { ...JSON.parse(canonicalStringify(legacy)), largeText: '汉'.repeat(n) };
  assert.ok(slotRecordBytes(1, migrateSnapshot(JSON.parse(canonicalStringify(belowLegacy))).snapshot) <= MAX_RECORD_BYTES);
  const belowBackup = backupOf(belowLegacy);
  const belowPrecheck = unwrap(await core.maintenance({ type: 'backup.precheck', payload: { backup: belowBackup } }));
  assert.equal(belowPrecheck.canRestore, true);
  const restored = unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup: belowBackup } }));
  assert.equal(restored.migrated, true);
  assert.equal(restored.restored, true);

  // 阈值上：按迁移后的 v2 记录计算，超限即拒绝
  const aboveLegacy = { ...JSON.parse(canonicalStringify(legacy)), largeText: '汉'.repeat(n + 4) };
  assert.ok(slotRecordBytes(1, migrateSnapshot(JSON.parse(canonicalStringify(aboveLegacy))).snapshot) > MAX_RECORD_BYTES);
  const abovePrecheck = await core.maintenance({ type: 'backup.precheck', payload: { backup: backupOf(aboveLegacy) } });
  assert.equal(abovePrecheck.ok, false);
  assert.equal(abovePrecheck.error.code, 'STORAGE_LIMIT');

  // 恢复成功样本的真实落盘记录不超过上限
  const pointer = JSON.parse(storage.values.get(KEYS.pointer));
  const record = JSON.parse(storage.values.get(pointer.slot === 'A' ? KEYS.A : KEYS.B));
  assert.equal(record.snapshot.schemaVersion, 2);
  assert.ok(slotRecordBytes(record.generation, record.snapshot) <= MAX_RECORD_BYTES);
});
