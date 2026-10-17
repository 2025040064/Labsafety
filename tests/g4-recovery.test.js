const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore, migrateSnapshot, BACKUP_KIND, BACKUP_VERSION } = require('../miniprogram/core/createLabSafeCore');
const { canonicalStringify, sha256 } = require('../miniprogram/core/hash');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { unwrap } = require('../miniprogram/pages/_shared/ui');

async function makeCore() {
  let counter = 0;
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `g4-${++counter}`
  });
  // 备份场景覆盖异常/附件上报与资料维护、状态推进命令，统一用管理员角色。
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });
  return core;
}

test('backup export and restore keep data, events and references consistent', async () => {
  const core = await makeCore();

  // 附件只记录元数据，导出 JSON 不含任何图片二进制
  unwrap(await core.execute({
    type: 'attachment.record', requestId: 'g4-att-1',
    payload: { entityType: 'Incident', entityId: 'inc-demo-01', fileName: '现场图片-1.jpg', size: 2048, localPath: 'wxfile://saved-1.jpg' }
  }));
  const exported = unwrap(await core.maintenance({ type: 'backup.export' }));
  const backupText = JSON.stringify(exported.backup);
  assert.equal(exported.backup.kind, BACKUP_KIND);
  assert.equal(exported.backup.backupVersion, BACKUP_VERSION);
  assert.ok(!backupText.includes('base64'));
  assert.ok(!backupText.toLowerCase().includes('data:image'));
  const attachmentRecord = exported.backup.snapshot.attachments[0];
  assert.deepEqual(Object.keys(attachmentRecord).sort(),
    ['entityId', 'entityType', 'fileName', 'id', 'isDemo', 'localPath', 'recordedAt', 'size']);
  assert.equal(attachmentRecord.localPath, 'wxfile://saved-1.jpg');

  // 导出后继续产生新数据，恢复应完整回到备份时刻
  unwrap(await core.execute({
    type: 'incident.report', requestId: 'g4-report-1',
    payload: { deviceId: 'dev-demo-03', title: '备份后新增异常', description: '恢复后应消失', severity: 'LOW' }
  }));
  assert.equal((await core.query({ type: 'dashboard.summary' })).incidentCount, 3);

  const restored = unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup: exported.backup } }));
  assert.equal(restored.restored, true);
  assert.equal(restored.migrated, false);
  assert.equal(restored.counts.incidents, 2);
  assert.equal(restored.counts.attachments, 1);

  // 恢复后数据/事件/引用一致
  const summary = unwrap(await core.query({ type: 'dashboard.summary' }));
  assert.equal(summary.incidentCount, 2);
  const detail = unwrap(await core.query({ type: 'incident.detail', payload: { incidentId: 'inc-demo-01' } }));
  assert.equal(detail.events.length, 1);
  assert.equal(detail.attachments.length, 1);
  assert.equal(detail.attachments[0].fileName, '现场图片-1.jpg');
  const citation = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '通风柜' } }));
  assert.equal(citation.status, 'FOUND');

  // 恢复后继续写入正常，附件可继续追加
  unwrap(await core.execute({
    type: 'attachment.record', requestId: 'g4-att-2',
    payload: { entityType: 'Incident', entityId: 'inc-demo-01', fileName: '现场图片-2.jpg', size: 4096, localPath: 'wxfile://saved-2.jpg' }
  }));
  const detailAfter = unwrap(await core.query({ type: 'incident.detail', payload: { incidentId: 'inc-demo-01' } }));
  assert.equal(detailAfter.attachments.length, 2);
});

test('corrupt or unsupported backups are refused without partial overwrite', async () => {
  const core = await makeCore();
  const exported = unwrap(await core.maintenance({ type: 'backup.export' }));

  // 先制造新状态，坏备份恢复失败后必须原样保留
  unwrap(await core.execute({
    type: 'incident.report', requestId: 'g4-report-2',
    payload: { deviceId: 'dev-demo-04', title: '坏备份前新增', description: '不得被部分覆盖', severity: 'LOW' }
  }));
  const before = unwrap(await core.query({ type: 'dashboard.summary' }));

  const tampered = { ...exported.backup, checksum: '0'.repeat(64) };
  const mismatch = await core.maintenance({ type: 'backup.restore', payload: { backup: tampered } });
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.error.code, 'BACKUP_CHECKSUM_MISMATCH');

  const wrongKind = { ...exported.backup, kind: 'other-backup' };
  assert.equal((await core.maintenance({ type: 'backup.precheck', payload: { backup: wrongKind } })).error.code, 'BACKUP_INVALID');

  const futureSchema = { ...exported.backup, snapshot: { ...exported.backup.snapshot, schemaVersion: 99 } };
  assert.equal((await core.maintenance({ type: 'backup.precheck', payload: { backup: futureSchema } })).error.code, 'BACKUP_UNSUPPORTED');

  assert.equal((await core.maintenance({ type: 'backup.restore', payload: { backup: null } })).error.code, 'BACKUP_INVALID');

  const after = unwrap(await core.query({ type: 'dashboard.summary' }));
  assert.deepEqual(after, before);
});

test('schema version 1 backup migrates cleanly on restore', async () => {
  const core = await makeCore();
  const legacy = createSeedSnapshot();
  legacy.schemaVersion = 1;
  delete legacy.inspectionTemplates;
  delete legacy.documents;
  delete legacy.documentVersions;
  delete legacy.documentChunks;
  delete legacy.attachments;
  const backup = {
    kind: BACKUP_KIND,
    backupVersion: BACKUP_VERSION,
    schemaVersion: 1,
    exportedAt: '2026-09-01T09:00:00+08:00',
    snapshot: legacy,
    checksum: sha256(canonicalStringify(legacy))
  };

  const precheck = unwrap(await core.maintenance({ type: 'backup.precheck', payload: { backup } }));
  assert.equal(precheck.canRestore, true);
  assert.equal(precheck.migrated, true);
  assert.equal(precheck.schemaVersion, 2);
  assert.equal(precheck.counts.devices, 4);

  // 预检不改动当前数据
  assert.equal((await core.query({ type: 'dashboard.summary' })).deviceCount, 4);

  const restored = unwrap(await core.maintenance({ type: 'backup.restore', payload: { backup } }));
  assert.equal(restored.restored, true);
  assert.equal(restored.migrated, true);

  // 迁移后的数据完全可读：台账、异常、知识集合为显式空集合
  const summary = unwrap(await core.query({ type: 'dashboard.summary' }));
  assert.deepEqual(summary, { deviceCount: 4, inspectionCount: 2, incidentCount: 2, openIncidentCount: 2 });
  assert.equal((await core.query({ type: 'inspection.template.list' })).items.length, 0);
  assert.equal((await core.query({ type: 'knowledge.document.list' })).items.length, 0);
  assert.equal((await core.query({ type: 'incident.list' })).items.length, 2);

  // 恢复即回到备份时刻的演示角色（种子默认学生）；切回管理员后继续推进状态
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });

  // 迁移后的快照继续可写
  const confirmed = await core.execute({
    type: 'incident.confirm', requestId: 'g4-legacy-1',
    payload: { incidentId: 'inc-demo-01', reason: '迁移后的数据仍可推进状态' }
  });
  assert.equal(confirmed.ok, true);
  assert.equal(confirmed.data.incident.status, 'CONFIRMED');
});

test('attachment metadata validates input and precheck warns on large backups', async () => {
  const core = await makeCore();

  const missingEntity = await core.execute({
    type: 'attachment.record', requestId: 'g4-att-3',
    payload: { entityType: 'Incident', entityId: 'inc-absent', fileName: 'x.jpg', size: 1 }
  });
  assert.equal(missingEntity.error.code, 'ENTITY_NOT_FOUND');
  const badSize = await core.execute({
    type: 'attachment.record', requestId: 'g4-att-4',
    payload: { entityType: 'Incident', entityId: 'inc-demo-01', fileName: 'x.jpg', size: 11 * 1024 * 1024 }
  });
  assert.equal(badSize.error.code, 'INVALID_INPUT');
  const badName = await core.execute({
    type: 'attachment.record', requestId: 'g4-att-5',
    payload: { entityType: 'Incident', entityId: 'inc-demo-01', fileName: '', size: 1 }
  });
  assert.equal(badName.error.code, 'INVALID_INPUT');

  // 无附件的正常导出：不产生附件缺失提示，容量告警为空
  const exported = unwrap(await core.maintenance({ type: 'backup.export' }));
  assert.deepEqual(exported.warnings, []);
  assert.ok(exported.byteSize > 0 && exported.byteSize < 900 * 1024);

  // 塞入大文档后触发容量告警
  const sections = Array.from({ length: 500 }, (_, index) => ({
    section: `章节-${index + 1}`,
    text: '容量压力测试正文'.padEnd(2000, '测')
  }));
  const imported = unwrap(await core.execute({
    type: 'document.import', requestId: 'g4-doc-1',
    payload: { title: '大容量演示规程', sections }
  }));
  unwrap(await core.execute({ type: 'document.publish', requestId: 'g4-doc-2', payload: { documentId: imported.document.id } }));
  const big = unwrap(await core.maintenance({ type: 'backup.export' }));
  assert.ok(big.byteSize > 900 * 1024);
  assert.ok(big.warnings.some(message => message.includes('1 MB')));

  // 迁移工具函数：非 v1 输入原样返回，不误标迁移
  assert.deepEqual(migrateSnapshot({ schemaVersion: 2 }), { snapshot: { schemaVersion: 2 }, migrated: false });
  assert.equal(migrateSnapshot(null).migrated, false);
});
