const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore } = require('../miniprogram/core/createLabSafeCore');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { unwrap } = require('../miniprogram/pages/_shared/ui');

test('assistant parses drafts locally, prompts on missing fields and submits only after confirmation', async () => {
  let counter = 0;
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `g3-${++counter}`
  });
  // 助手场景覆盖学生上报/巡检流，同时需要构造停用设备（管理员动作），先用管理员角色。
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });

  const before = unwrap(await core.query({ type: 'dashboard.summary' }));

  // 缺设备字段：只提示，不写入，不自动补写
  const incomplete = unwrap(await core.query({ type: 'assistant.parseDraft', payload: { text: '设备好像有故障' } }));
  assert.equal(incomplete.intent, 'INCIDENT');
  assert.equal(incomplete.valid, false);
  assert.ok(incomplete.missing.some(message => message.includes('设备')));
  assert.equal(incomplete.draft.deviceId, null);
  const after = unwrap(await core.query({ type: 'dashboard.summary' }));
  assert.deepEqual(after, before);

  // 同一输入可重复回放，结果一致
  const replay = unwrap(await core.query({ type: 'assistant.parseDraft', payload: { text: '设备好像有故障' } }));
  assert.deepEqual(replay, incomplete);

  // 未知意图只给提示
  const unknown = unwrap(await core.query({ type: 'assistant.parseDraft', payload: { text: '今天天气不错' } }));
  assert.equal(unknown.intent, 'UNKNOWN');
  assert.equal(unknown.valid, false);
  assert.ok(unknown.message.includes('异常') || unknown.message.includes('巡检'));

  // 补全设备后：设备、严重度规则与草稿内容符合本地规则
  const draft = unwrap(await core.query({
    type: 'assistant.parseDraft',
    payload: { text: 'SIM-DEV-002 温度显示异常，疑似故障，情况紧急' }
  }));
  assert.equal(draft.valid, true);
  assert.deepEqual(draft.missing, []);
  assert.equal(draft.draft.deviceId, 'dev-demo-02');
  assert.equal(draft.draft.deviceName, '模拟恒温箱');
  assert.equal(draft.draft.severity, 'HIGH');

  // 用户确认后，用既有业务命令提交
  const committed = unwrap(await core.execute({
    type: 'incident.report', requestId: 'g3-report-1',
    payload: {
      deviceId: draft.draft.deviceId,
      title: draft.draft.title,
      description: draft.draft.description,
      severity: draft.draft.severity
    }
  }));
  assert.equal(committed.incident.status, 'REPORTED');

  // 巡检草稿：按设备匹配模板，逐项预填合格结论
  const inspection = unwrap(await core.query({
    type: 'assistant.parseDraft', payload: { text: '巡检 SIM-DEV-001' }
  }));
  assert.equal(inspection.intent, 'INSPECTION');
  assert.equal(inspection.valid, true);
  assert.equal(inspection.draft.templateId, 'tmpl-demo-01');
  assert.equal(inspection.draft.results.length, 3);
  const submitted = unwrap(await core.execute({
    type: 'inspection.submit', requestId: 'g3-insp-1',
    payload: {
      templateId: inspection.draft.templateId,
      deviceId: inspection.draft.deviceId,
      results: inspection.draft.results
    }
  }));
  assert.equal(submitted.inspection.status, 'SUBMITTED');

  // 无效状态被业务层拒绝：停用设备后，草稿提交仍走核心并被拒绝
  const created = unwrap(await core.execute({
    type: 'device.create', requestId: 'g3-dev-1',
    payload: { code: 'G3-DEV-01', name: '助手演示设备', labId: 'lab-demo-01' }
  }));
  unwrap(await core.execute({ type: 'device.disable', requestId: 'g3-dev-2', payload: { deviceId: created.device.id } }));

  // 学生角色的草稿提交仍走同一命令；切回学生验证上报被业务规则而非角色拒绝
  await core.execute({ type: 'demo.role.switch', payload: { role: 'student' } });

  const staleDraft = unwrap(await core.query({
    type: 'assistant.parseDraft', payload: { text: 'G3-DEV-01 异常停转' }
  }));
  assert.equal(staleDraft.valid, true);
  const rejected = await core.execute({
    type: 'incident.report', requestId: 'g3-report-2',
    payload: {
      deviceId: staleDraft.draft.deviceId,
      title: staleDraft.draft.title,
      description: staleDraft.draft.description,
      severity: 'LOW'
    }
  });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error.code, 'DEVICE_UNAVAILABLE');
});
