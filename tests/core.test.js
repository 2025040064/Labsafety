const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore } = require('../miniprogram/core/createLabSafeCore');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');

function makeCore() {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  let counter = 0;
  const core = createLabSafeCore({
    repository,
    clock: () => '2026-10-15T10:00:00+08:00',
    createId: () => `test-${++counter}`
  });
  return { core, repository };
}

test('seed provides fictional dashboard, devices and incident timeline', async () => {
  const { core } = makeCore();
  assert.deepEqual(await core.query({ type: 'dashboard.summary' }), {
    deviceCount: 4, inspectionCount: 2, incidentCount: 2, openIncidentCount: 2
  });
  assert.equal((await core.query({ type: 'device.list' })).items[0].labName, '模拟实验室 A');
  const detail = await core.query({ type: 'incident.detail', payload: { incidentId: 'inc-demo-01' } });
  assert.equal(detail.incident.code, 'SIM-INC-001');
  assert.equal(detail.events[0].action, 'REPORT');
});

test('report creates incident and timeline atomically and is idempotent', async () => {
  const { core, repository } = makeCore();
  const command = {
    type: 'incident.report', requestId: 'request-1',
    payload: { deviceId: 'dev-demo-01', title: '模拟噪声异常', description: '测试现象', severity: 'MEDIUM' }
  };
  const first = await core.execute(command);
  assert.equal(first.ok, true);
  assert.equal(first.data.incident.status, 'REPORTED');
  assert.equal(first.data.incident.code, 'SIM-INC-003');
  assert.equal((await core.query({ type: 'incident.list' })).items.length, 3);
  await core.execute({ type: 'demo.role.switch', payload: { role: 'teacher' } });
  const generation = (await repository.load()).generation;
  const replay = await core.execute(command);
  assert.deepEqual(replay, first);
  assert.equal((await repository.load()).generation, generation);
  const detail = await core.query({ type: 'incident.detail', payload: { incidentId: first.data.incident.id } });
  assert.equal(detail.events.length, 1);
  assert.equal(detail.events[0].entityId, first.data.incident.id);
  const conflict = await core.execute({ ...command, payload: { ...command.payload, title: '另一异常' } });
  assert.equal(conflict.error.code, 'REQUEST_ID_REUSED');
  const unsafeId = await core.execute({ ...command, requestId: '__proto__' });
  assert.equal(unsafeId.error.code, 'INVALID_REQUEST_ID');
});

test('invalid device does not mutate and reset restores seed', async () => {
  const { core, repository } = makeCore();
  const before = (await repository.load()).generation;
  const bad = await core.execute({ type: 'incident.report', requestId: 'bad-1', payload: { deviceId: 'absent', title: '模拟异常', severity: 'LOW' } });
  assert.equal(bad.error.code, 'DEVICE_UNAVAILABLE');
  assert.equal((await repository.load()).generation, before);
  assert.equal((await core.execute({ type: 'demo.role.switch', payload: { role: 'teacher' } })).ok, true);
  assert.equal((await core.query({ type: 'demo.session' })).label, '教师');
  assert.equal((await core.maintenance({ type: 'demo.reset' })).ok, true);
  assert.equal((await core.query({ type: 'demo.session' })).role, 'student');
  assert.equal((await core.query({ type: 'incident.list' })).items.length, 2);
});

test('concurrent retry with the same requestId returns the committed record once', async () => {
  const { core } = makeCore();
  const command = {
    type: 'incident.report', requestId: 'parallel-1',
    payload: { deviceId: 'dev-demo-03', title: '模拟称量异常', severity: 'LOW' }
  };
  const [a, b] = await Promise.all([core.execute(command), core.execute(command)]);
  assert.equal(a.ok, true);
  assert.deepEqual(a, b);
  assert.equal((await core.query({ type: 'incident.list' })).items.length, 3);
});

test('knowledge search cites seeded version and refuses unsupported query', async () => {
  const { core } = makeCore();
  const found = await core.query({ type: 'knowledge.search', payload: { text: '通风柜' } });
  assert.equal(found.status, 'FOUND');
  assert.equal(found.items[0].source.version, '演示 v1');
  const absent = await core.query({ type: 'knowledge.search', payload: { text: '不存在的化学品规定' } });
  assert.deepEqual(absent, { status: 'NO_EVIDENCE', items: [] });
});
