const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore, INCIDENT_TRANSITIONS } = require('../miniprogram/core/createLabSafeCore');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');

let requestCounter = 0;
/** Unique, pattern-valid requestId per command so no test accidentally replays another one. */
function requestId(prefix) { return `${prefix}-req-${++requestCounter}`; }

async function makeCore() {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  let counter = 0;
  const core = createLabSafeCore({
    repository,
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `g1-${++counter}`
  });
  // 本文件覆盖状态机与台账规则本身，统一用管理员角色；角色拒绝规则由专用测试覆盖。
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });
  return { core, repository };
}

const STEPS_TO_STATE = {
  REPORTED: [],
  CONFIRMED: ['incident.confirm'],
  REJECTED: ['incident.reject'],
  ASSIGNED: ['incident.confirm', 'incident.assign'],
  IN_PROGRESS: ['incident.confirm', 'incident.assign', 'incident.start'],
  PENDING_REVIEW: ['incident.confirm', 'incident.assign', 'incident.start', 'incident.submitReview'],
  CLOSED: ['incident.confirm', 'incident.assign', 'incident.start', 'incident.submitReview', 'incident.close']
};

/** Report a fresh incident and drive it along legal edges until it reaches `state`. */
async function incidentInState(core, state) {
  const created = await core.execute({
    type: 'incident.report', requestId: requestId('drive'),
    payload: { deviceId: 'dev-demo-03', title: `驱动到 ${state}`, severity: 'LOW' }
  });
  assert.equal(created.ok, true, JSON.stringify(created.error));
  const incidentId = created.data.incident.id;
  for (const type of STEPS_TO_STATE[state]) {
    const step = await core.execute({ type, requestId: requestId('drive'), payload: { incidentId, reason: `驱动状态 ${type}` } });
    assert.equal(step.ok, true, `${type} 应能驱动到 ${state}：${JSON.stringify(step.error)}`);
  }
  return incidentId;
}

test('incident walks the full legal path with one event and one revision per edge', async () => {
  const { core } = await makeCore();
  const path = ['incident.confirm', 'incident.assign', 'incident.start', 'incident.submitReview', 'incident.return', 'incident.submitReview', 'incident.close'];
  const expected = ['CONFIRMED', 'ASSIGNED', 'IN_PROGRESS', 'PENDING_REVIEW', 'IN_PROGRESS', 'PENDING_REVIEW', 'CLOSED'];
  let revision = 1;
  for (let index = 0; index < path.length; index += 1) {
    const result = await core.execute({
      type: path[index], requestId: requestId('flow'),
      payload: { incidentId: 'inc-demo-01', reason: `处理步骤 ${index + 1}`, expectedRevision: revision }
    });
    assert.equal(result.ok, true, `${path[index]} 失败：${JSON.stringify(result.error)}`);
    revision += 1;
    assert.equal(result.data.incident.status, expected[index]);
    assert.equal(result.data.incident.revision, revision);
    assert.equal(result.data.event.action, INCIDENT_TRANSITIONS[path[index]].action);
    assert.equal(result.data.event.actorRole, 'admin');
  }
  const detail = await core.query({ type: 'incident.detail', payload: { incidentId: 'inc-demo-01' } });
  assert.deepEqual(detail.events.map(item => item.action),
    ['REPORT', 'CONFIRM', 'ASSIGN', 'START', 'SUBMIT_REVIEW', 'RETURN', 'SUBMIT_REVIEW', 'CLOSE']);
  assert.equal(detail.events.length, detail.incident.revision);
  assert.equal((await core.query({ type: 'dashboard.summary' })).openIncidentCount, 1);
});

test('incident state machine accepts exactly the declared edges from every status', async () => {
  for (const state of Object.keys(STEPS_TO_STATE)) {
    const { core } = await makeCore();
    for (const type of Object.keys(INCIDENT_TRANSITIONS)) {
      const incidentId = await incidentInState(core, state);
      const before = await core.query({ type: 'incident.detail', payload: { incidentId } });
      assert.equal(before.events.length, before.incident.revision);
      const result = await core.execute({ type, requestId: requestId('matrix'), payload: { incidentId, reason: `${state} 下尝试 ${type}` } });
      const shouldPass = INCIDENT_TRANSITIONS[type].allowedFrom.includes(state);
      assert.equal(result.ok, shouldPass, `${state} + ${type} 预期 ${shouldPass}`);
      const after = await core.query({ type: 'incident.detail', payload: { incidentId } });
      if (shouldPass) {
        assert.equal(result.data.incident.status, INCIDENT_TRANSITIONS[type].target);
        assert.equal(result.data.incident.revision, before.incident.revision + 1);
        assert.equal(result.data.event.from, state);
        assert.equal(result.data.event.to, INCIDENT_TRANSITIONS[type].target);
        assert.equal(after.events.length, after.incident.revision);
        const recorded = after.events.find(item => item.id === result.data.event.id);
        assert.equal(recorded.action, INCIDENT_TRANSITIONS[type].action);
        assert.equal(recorded.from, state);
        assert.equal(recorded.to, INCIDENT_TRANSITIONS[type].target);
      } else {
        assert.equal(result.error.code, 'INVALID_TRANSITION');
        assert.equal(after.incident.status, state);
        assert.equal(after.incident.revision, before.incident.revision);
        assert.equal(after.events.length, before.events.length);
      }
    }
  }
});

test('rejected transition never commits, and revision guard detects a stale editor', async () => {
  const { core, repository } = await makeCore();
  const generation = (await repository.load()).generation;
  const skipped = await core.execute({ type: 'incident.close', requestId: requestId('skip'), payload: { incidentId: 'inc-demo-01', reason: '试图跳步关闭' } });
  assert.equal(skipped.error.code, 'INVALID_TRANSITION');
  assert.equal((await repository.load()).generation, generation);

  const command = { type: 'incident.confirm', requestId: requestId('confirm'), payload: { incidentId: 'inc-demo-01', reason: '已核实模拟异常', expectedRevision: 1 } };
  const first = await core.execute(command);
  assert.equal(first.ok, true);
  assert.equal(first.data.incident.status, 'CONFIRMED');
  assert.equal(first.data.incident.revision, 2);
  const committed = (await repository.load()).generation;
  const replay = await core.execute(command);
  assert.deepEqual(replay, first);
  assert.equal((await repository.load()).generation, committed);

  const stale = await core.execute({ type: 'incident.assign', requestId: requestId('assign'), payload: { incidentId: 'inc-demo-01', reason: '按旧版本分派', expectedRevision: 1 } });
  assert.equal(stale.error.code, 'REVISION_CONFLICT');
  const fresh = await core.execute({ type: 'incident.assign', requestId: requestId('assign'), payload: { incidentId: 'inc-demo-01', reason: '按新版本分派', expectedRevision: 2 } });
  assert.equal(fresh.data.incident.status, 'ASSIGNED');
  const unguarded = await core.execute({ type: 'incident.start', requestId: requestId('start'), payload: { incidentId: 'inc-demo-01', reason: '不带版本号也可推进' } });
  assert.equal(unguarded.data.incident.status, 'IN_PROGRESS');
});

test('incident commands validate identifiers, reason bounds and requestId format', async () => {
  const { core } = await makeCore();
  const missing = await core.execute({ type: 'incident.confirm', requestId: requestId('bad'), payload: { incidentId: 'inc-absent', reason: '不存在的异常' } });
  assert.equal(missing.error.code, 'INCIDENT_NOT_FOUND');
  const shortReason = await core.execute({ type: 'incident.confirm', requestId: requestId('bad'), payload: { incidentId: 'inc-demo-02', reason: '短' } });
  assert.equal(shortReason.error.code, 'INVALID_INPUT');
  const longReason = await core.execute({ type: 'incident.confirm', requestId: requestId('bad'), payload: { incidentId: 'inc-demo-02', reason: '长'.repeat(501) } });
  assert.equal(longReason.error.code, 'INVALID_INPUT');
  const noReason = await core.execute({ type: 'incident.confirm', requestId: requestId('bad'), payload: { incidentId: 'inc-demo-02' } });
  assert.equal(noReason.error.code, 'INVALID_INPUT');
  const unsafeId = await core.execute({ type: 'incident.confirm', requestId: '__proto__', payload: { incidentId: 'inc-demo-02', reason: '合法说明' } });
  assert.equal(unsafeId.error.code, 'INVALID_REQUEST_ID');
  const noRequestId = await core.execute({ type: 'incident.confirm', payload: { incidentId: 'inc-demo-02', reason: '合法说明' } });
  assert.equal(noRequestId.error.code, 'INVALID_REQUEST_ID');
  const badRevision = await core.execute({ type: 'incident.assign', requestId: requestId('bad'), payload: { incidentId: 'inc-demo-02', reason: '版本号非法', expectedRevision: '2' } });
  assert.equal(badRevision.error.code, 'INVALID_INPUT');
  const reused = await core.execute({ type: 'incident.assign', requestId: 'g1-reused', payload: { incidentId: 'inc-demo-02', reason: '第一次分派' } });
  assert.equal(reused.ok, true);
  const conflicting = await core.execute({ type: 'incident.assign', requestId: 'g1-reused', payload: { incidentId: 'inc-demo-02', reason: '第二次分派' } });
  assert.equal(conflicting.error.code, 'REQUEST_ID_REUSED');
  assert.equal((await core.execute({ type: 'lab.destroy', requestId: requestId('bad') })).error.code, 'UNKNOWN_COMMAND');
  await assert.rejects(core.query({ type: 'lab.mystery' }), error => error.code === 'UNKNOWN_QUERY');
});

test('lab and device ledger edits enforce unique codes and disable rules', async () => {
  const { core } = await makeCore();
  const lab = await core.execute({ type: 'lab.create', requestId: requestId('lab'), payload: { code: 'sim-lab-03', name: '模拟实验室 C', location: '虚构校区 C 栋' } });
  assert.equal(lab.ok, true);
  assert.equal(lab.data.lab.code, 'SIM-LAB-03');
  assert.equal(lab.data.lab.status, 'ACTIVE');
  assert.equal(lab.data.lab.deviceCount, 0);
  assert.equal((await core.execute({ type: 'lab.create', requestId: requestId('lab'), payload: { code: 'SIM-LAB-01', name: '重复编号实验室' } })).error.code, 'CODE_DUPLICATE');
  assert.equal((await core.execute({ type: 'lab.create', requestId: requestId('lab'), payload: { code: '非法 编号', name: '模拟实验室 D' } })).error.code, 'INVALID_INPUT');
  assert.equal((await core.execute({ type: 'lab.create', requestId: requestId('lab'), payload: { code: 'SIM-LAB-04', name: '名' } })).error.code, 'INVALID_INPUT');

  const renamed = await core.execute({ type: 'lab.update', requestId: requestId('lab'), payload: { labId: lab.data.lab.id, name: '模拟实验室 C（改）' } });
  assert.equal(renamed.data.lab.name, '模拟实验室 C（改）');
  assert.equal(renamed.data.lab.code, 'SIM-LAB-03');
  assert.equal((await core.execute({ type: 'lab.update', requestId: requestId('lab'), payload: { labId: lab.data.lab.id, code: 'sim-lab-02' } })).error.code, 'CODE_DUPLICATE');
  assert.equal((await core.execute({ type: 'lab.update', requestId: requestId('lab'), payload: { labId: lab.data.lab.id } })).error.code, 'INVALID_INPUT');
  assert.equal((await core.execute({ type: 'lab.update', requestId: requestId('lab'), payload: { labId: 'lab-absent', name: '不存在的实验室' } })).error.code, 'LAB_NOT_FOUND');
  assert.equal((await core.execute({ type: 'lab.update', requestId: requestId('lab'), payload: { labId: lab.data.lab.id, code: 'SIM-LAB-03' } })).data.lab.code, 'SIM-LAB-03');

  const device = await core.execute({ type: 'device.create', requestId: requestId('dev'), payload: { code: 'sim-dev-005', name: '模拟反应釜', labId: lab.data.lab.id, model: 'DEMO-RX-05' } });
  assert.equal(device.ok, true);
  assert.equal(device.data.device.code, 'SIM-DEV-005');
  assert.equal(device.data.device.labName, '模拟实验室 C（改）');
  assert.equal((await core.execute({ type: 'device.create', requestId: requestId('dev'), payload: { code: 'SIM-DEV-001', name: '重复设备', labId: 'lab-demo-01' } })).error.code, 'CODE_DUPLICATE');
  assert.equal((await core.execute({ type: 'device.create', requestId: requestId('dev'), payload: { code: 'SIM-DEV-006', name: '无实验室设备', labId: 'lab-absent' } })).error.code, 'LAB_NOT_FOUND');
  assert.equal((await core.execute({ type: 'device.create', requestId: requestId('dev'), payload: { code: 'SIM-DEV-007', name: '缺编号设备', labId: '' } })).error.code, 'INVALID_INPUT');
  assert.equal((await core.query({ type: 'lab.detail', payload: { labId: lab.data.lab.id } })).devices.length, 1);
  assert.deepEqual(await core.query({ type: 'lab.detail', payload: { labId: 'lab-absent' } }), { lab: null, devices: [] });

  const moved = await core.execute({ type: 'device.update', requestId: requestId('dev'), payload: { deviceId: device.data.device.id, labId: 'lab-demo-02', model: 'DEMO-RX-05B' } });
  assert.equal(moved.data.device.labId, 'lab-demo-02');
  assert.equal(moved.data.device.model, 'DEMO-RX-05B');
  assert.equal(moved.data.device.labName, '模拟实验室 B');
  assert.equal((await core.query({ type: 'lab.detail', payload: { labId: 'lab-demo-02' } })).devices.length, 2);
  assert.equal((await core.execute({ type: 'device.update', requestId: requestId('dev'), payload: { deviceId: device.data.device.id, labId: 'lab-absent' } })).error.code, 'LAB_NOT_FOUND');
  assert.equal((await core.execute({ type: 'device.update', requestId: requestId('dev'), payload: { deviceId: device.data.device.id, code: 'SIM-DEV-004' } })).error.code, 'CODE_DUPLICATE');
  assert.equal((await core.execute({ type: 'device.update', requestId: requestId('dev'), payload: { deviceId: 'dev-absent', name: '不存在的设备' } })).error.code, 'DEVICE_NOT_FOUND');

  const disabled = await core.execute({ type: 'device.disable', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-01' } });
  assert.equal(disabled.ok, true);
  assert.equal(disabled.data.device.status, 'INACTIVE');
  assert.equal(disabled.data.cancelledInspectionCount, 1);
  const cancelled = await core.query({ type: 'inspection.list', payload: { filter: { deviceId: 'dev-demo-01', status: 'CANCELLED' } } });
  assert.deepEqual(cancelled.items.map(item => item.id), ['insp-demo-01']);
  assert.equal((await core.execute({ type: 'device.disable', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-01' } })).error.code, 'INVALID_TRANSITION');
  assert.equal((await core.execute({ type: 'device.update', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-01', name: '停用后改名' } })).error.code, 'DEVICE_INACTIVE');
  assert.equal((await core.execute({ type: 'incident.report', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-01', title: '停用设备上报', severity: 'LOW' } })).error.code, 'DEVICE_UNAVAILABLE');
  assert.equal((await core.execute({ type: 'device.enable', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-02' } })).error.code, 'INVALID_TRANSITION');
  assert.equal((await core.execute({ type: 'device.enable', requestId: requestId('dev'), payload: { deviceId: 'dev-demo-01' } })).data.device.status, 'ACTIVE');
  assert.equal((await core.query({ type: 'device.detail', payload: { deviceId: 'dev-demo-01' } })).device.status, 'ACTIVE');
});

test('inspection submission validates the template, stays idempotent and lists by filter', async () => {
  const { core } = await makeCore();
  const templates = await core.query({ type: 'inspection.template.list' });
  assert.deepEqual(templates.items.map(item => item.id), ['tmpl-demo-01', 'tmpl-demo-02']);
  assert.deepEqual(templates.items[0].items, ['外观检查', '风速是否正常', '排风管道有无泄漏']);

  const results = templates.items[0].items.map((item, index) => ({ item, passed: index !== 1, note: index === 1 ? '模拟风速偏低' : '' }));
  const payload = { templateId: 'tmpl-demo-01', deviceId: 'dev-demo-01', results };
  const submitted = await core.execute({ type: 'inspection.submit', requestId: 'g1-inspection-1', payload });
  assert.equal(submitted.ok, true);
  assert.equal(submitted.data.inspection.status, 'SUBMITTED');
  assert.equal(submitted.data.inspection.title, '通风柜日常巡检');
  assert.equal(submitted.data.inspection.deviceName, '模拟通风柜');
  assert.equal(submitted.data.inspection.results.length, 3);
  assert.deepEqual(await core.execute({ type: 'inspection.submit', requestId: 'g1-inspection-1', payload }), submitted);

  const detail = await core.query({ type: 'inspection.detail', payload: { inspectionId: submitted.data.inspection.id } });
  assert.equal(detail.template.id, 'tmpl-demo-01');
  assert.deepEqual(detail.events.map(item => item.action), ['SUBMIT']);
  assert.equal(detail.events[0].entityType, 'Inspection');
  assert.deepEqual(await core.query({ type: 'inspection.detail', payload: { inspectionId: 'insp-absent' } }), { inspection: null, template: null, events: [] });
  assert.equal((await core.query({ type: 'inspection.detail', payload: { inspectionId: 'insp-demo-01' } })).template, null);

  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-demo-01', deviceId: 'dev-demo-01', results: results.slice(0, 2) } })).error.code, 'INVALID_INPUT');
  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-absent', deviceId: 'dev-demo-01', results } })).error.code, 'TEMPLATE_NOT_FOUND');
  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-demo-01', deviceId: 'dev-absent', results } })).error.code, 'DEVICE_NOT_FOUND');
  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-demo-02', deviceId: 'dev-demo-02', results: [{ item: '温度显示是否正常', passed: 'yes' }] } })).error.code, 'INVALID_INPUT');
  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-demo-02', deviceId: 'dev-demo-02', results: {} } })).error.code, 'INVALID_INPUT');
  await core.execute({ type: 'device.disable', requestId: requestId('insp'), payload: { deviceId: 'dev-demo-02' } });
  assert.equal((await core.execute({ type: 'inspection.submit', requestId: requestId('insp'), payload: { templateId: 'tmpl-demo-02', deviceId: 'dev-demo-02', results: templates.items[1].items.map(item => ({ item, passed: true })) } })).error.code, 'DEVICE_INACTIVE');

  const submittedList = await core.query({ type: 'inspection.list', payload: { filter: { status: 'SUBMITTED' } } });
  assert.deepEqual(submittedList.items.map(item => item.id).sort(), ['insp-demo-02', submitted.data.inspection.id].sort());
  assert.equal((await core.query({ type: 'inspection.list' })).items.length, 3);
  assert.equal((await core.query({ type: 'inspection.list', payload: { filter: {} } })).items.length, 3);
});

test('list filters narrow devices and incidents without changing the unfiltered contract', async () => {
  const { core } = await makeCore();
  assert.deepEqual((await core.query({ type: 'lab.list' })).items.map(item => [item.id, item.deviceCount]),
    [['lab-demo-01', 3], ['lab-demo-02', 1]]);
  assert.equal((await core.query({ type: 'device.list' })).items.length, 4);
  assert.deepEqual((await core.query({ type: 'device.list', payload: { filter: { labId: 'lab-demo-02' } } })).items.map(item => item.id), ['dev-demo-04']);
  assert.deepEqual((await core.query({ type: 'device.list', payload: { filter: { keyword: 'sim-dev-004' } } })).items.map(item => item.id), ['dev-demo-04']);
  assert.deepEqual((await core.query({ type: 'device.list', payload: { filter: { keyword: '模拟' } } })).items.map(item => item.id),
    ['dev-demo-01', 'dev-demo-02', 'dev-demo-03', 'dev-demo-04']);
  assert.equal((await core.query({ type: 'device.list', payload: { filter: { labId: 'lab-demo-01', status: 'INACTIVE' } } })).items.length, 0);
  assert.deepEqual((await core.query({ type: 'device.list', payload: { filter: { labId: 'lab-demo-01', keyword: '恒温' } } })).items.map(item => item.id), ['dev-demo-02']);

  assert.equal((await core.query({ type: 'incident.list' })).items.length, 2);
  assert.deepEqual((await core.query({ type: 'incident.list', payload: { filter: { status: 'CONFIRMED' } } })).items.map(item => item.id), ['inc-demo-02']);
  assert.deepEqual((await core.query({ type: 'incident.list', payload: { filter: { deviceId: 'dev-demo-02' } } })).items.map(item => item.id), ['inc-demo-01']);
  assert.deepEqual((await core.query({ type: 'incident.list', payload: { filter: { severity: 'low' } } })).items.map(item => item.id), ['inc-demo-01']);
  assert.equal((await core.query({ type: 'incident.list', payload: { filter: { deviceId: 'dev-demo-02', severity: 'MEDIUM' } } })).items.length, 0);
  assert.deepEqual((await core.query({ type: 'incident.list', payload: { filter: { status: 'REPORTED', deviceId: 'dev-demo-02', severity: 'LOW' } } })).items.map(item => item.id), ['inc-demo-01']);
  assert.equal((await core.query({ type: 'incident.list', payload: { filter: { status: '' } } })).items.length, 2);
});

test('demo role matrix gates write commands as local demo rules, not real permissions', async () => {
  const { core } = await makeCore();
  // 回到种子默认的学生角色
  unwrap2(await core.execute({ type: 'demo.role.switch', payload: { role: 'student' } }));

  // 学生可上报异常、提交巡检、记录附件
  const reported = await core.execute({
    type: 'incident.report', requestId: 'role-report-1',
    payload: { deviceId: 'dev-demo-01', title: '学生上报模拟异常', severity: 'LOW' }
  });
  assert.equal(reported.ok, true);
  const studentConfirm = await core.execute({
    type: 'incident.confirm', requestId: 'role-confirm-1',
    payload: { incidentId: reported.data.incident.id, reason: '学生不能推进状态' }
  });
  assert.equal(studentConfirm.error.code, 'ROLE_FORBIDDEN');
  const studentLedger = await core.execute({
    type: 'lab.create', requestId: 'role-lab-1',
    payload: { code: 'ROLE-LAB-01', name: '学生不能建实验室' }
  });
  assert.equal(studentLedger.error.code, 'ROLE_FORBIDDEN');

  // 教师可推进状态、维护资料，但不能编辑台账
  await core.execute({ type: 'demo.role.switch', payload: { role: 'teacher' } });
  const confirmed = await core.execute({
    type: 'incident.confirm', requestId: 'role-confirm-2',
    payload: { incidentId: reported.data.incident.id, reason: '教师确认模拟异常' }
  });
  assert.equal(confirmed.ok, true);
  const imported = await core.execute({
    type: 'document.import', requestId: 'role-doc-1',
    payload: { title: '教师导入的演示规程', sections: [{ section: '使用', text: '教师角色可以维护资料。' }] }
  });
  assert.equal(imported.ok, true);
  const teacherLedger = await core.execute({
    type: 'device.create', requestId: 'role-dev-1',
    payload: { code: 'ROLE-DEV-01', name: '教师不能建设备', labId: 'lab-demo-01' }
  });
  assert.equal(teacherLedger.error.code, 'ROLE_FORBIDDEN');

  // 管理员是超集：台账可编辑
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });
  const adminLab = await core.execute({
    type: 'lab.create', requestId: 'role-lab-2',
    payload: { code: 'ROLE-LAB-02', name: '管理员演示实验室' }
  });
  assert.equal(adminLab.ok, true);

  // 角色切换本身始终可用，且拒绝不产生写入
  assert.equal((await core.query({ type: 'demo.session' })).role, 'admin');
});

function unwrap2(result) {
  if (!result.ok) throw new Error(JSON.stringify(result.error));
  return result.data;
}

test('schema version 1 data stays readable, version 99 is refused, reset upgrades to seed', async () => {
  const legacy = createSeedSnapshot();
  legacy.schemaVersion = 1;
  delete legacy.inspectionTemplates;
  let generation = 0;
  let stored = legacy;
  const repository = {
    async load() { return { generation, snapshot: JSON.parse(JSON.stringify(stored)) }; },
    async commit(expected, next) {
      if (expected !== generation) { const error = new Error('stale'); error.code = 'GENERATION_CONFLICT'; throw error; }
      stored = JSON.parse(JSON.stringify(next));
      generation += 1;
      return { generation };
    }
  };
  const core = createLabSafeCore({ repository, clock: () => '2026-10-16T10:00:00+08:00', createId: () => 'legacy-id' });
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });
  assert.equal((await core.query({ type: 'inspection.template.list' })).items.length, 0);
  assert.equal((await core.query({ type: 'device.list' })).items.length, 4);
  assert.equal((await core.execute({ type: 'incident.confirm', requestId: requestId('legacy'), payload: { incidentId: 'inc-demo-01', reason: '旧版本数据仍可确认' } })).ok, true);
  assert.equal((await core.maintenance({ type: 'demo.reset' })).ok, true);
  assert.equal(stored.schemaVersion, 2);
  assert.equal((await core.query({ type: 'inspection.template.list' })).items.length, 2);
  assert.equal((await core.query({ type: 'incident.list' })).items.length, 2);

  const unsupported = createLabSafeCore({
    repository: { async load() { return { generation: 0, snapshot: { schemaVersion: 99 } }; }, async commit() { return { generation: 1 }; } },
    clock: () => '2026-10-16T10:00:00+08:00'
  });
  await assert.rejects(unsupported.query({ type: 'dashboard.summary' }), error => error.code === 'SCHEMA_UNSUPPORTED');
  const refused = await unsupported.execute({ type: 'incident.confirm', requestId: requestId('legacy'), payload: { incidentId: 'inc-demo-01', reason: '版本不受支持' } });
  assert.equal(refused.error.code, 'SCHEMA_UNSUPPORTED');
});
