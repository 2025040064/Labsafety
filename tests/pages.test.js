const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore } = require('../miniprogram/core/createLabSafeCore');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { unwrap } = require('../miniprogram/pages/_shared/ui');

function pageAt(path) {
  let definition;
  global.Page = page => { definition = page; };
  delete require.cache[require.resolve(path)];
  require(path);
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) {
      // 与微信 setData 一致支持路径键：'a.b' 与 'list[0].c' 分别写入嵌套字段
      for (const key of Object.keys(patch)) {
        const path = key.replace(/\[(\d+)\]/g, '.$1').split('.');
        let target = this.data;
        for (let i = 0; i < path.length - 1; i++) target = target[path[i]];
        target[path[path.length - 1]] = patch[key];
      }
    }
  };
}

test('actual device page reports incident and actual detail page reads timeline', async () => {
  let counter = 0;
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-15T10:00:00+08:00',
    createId: () => `ui-${++counter}`
  });
  global.getApp = () => ({ corePromise: Promise.resolve(core) });
  let destination = '';
  global.wx = {
    redirectTo({ url }) { destination = url; },
    switchTab({ url }) { destination = url; }
  };
  const devicePage = pageAt('../miniprogram/pages/device-detail/index.js');
  devicePage.onLoad({ id: 'dev-demo-01' });
  await devicePage.load();
  assert.equal(devicePage.data.device.code, 'SIM-DEV-001');
  devicePage.onTitleInput({ detail: { value: '模拟设备异常' } });
  devicePage.onDescriptionInput({ detail: { value: '观察到模拟噪声' } });
  await devicePage.reportIncident();
  assert.match(destination, /^\/pages\/incident-detail\/index\?id=/);
  const incidentId = decodeURIComponent(destination.split('id=')[1]);
  const detailPage = pageAt('../miniprogram/pages/incident-detail/index.js');
  detailPage.onLoad({ id: encodeURIComponent(incidentId) });
  await detailPage.load();
  assert.equal(detailPage.data.incident.title, '模拟设备异常');
  assert.equal(detailPage.data.events.length, 1);
  assert.equal(detailPage.data.events[0].actionText, '上报异常');
});

test('management pages drive ledger edits, state transitions and inspection submission', async () => {
  let counter = 0;
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `ui2-${++counter}`
  });
  // 管理页面覆盖台账编辑（管理员）与状态推进（教师/管理员）命令，统一用管理员角色。
  await core.execute({ type: 'demo.role.switch', payload: { role: 'admin' } });
  global.getApp = () => ({ corePromise: Promise.resolve(core) });
  const toasts = [];
  global.wx = {
    navigateTo() {},
    redirectTo() {},
    switchTab() {},
    showToast(options) { toasts.push(options && options.title); }
  };

  // devices page: create a lab, reject a duplicate code, then register a device in it
  const devicesPage = pageAt('../miniprogram/pages/devices/index.js');
  await devicesPage.load();
  devicesPage.toggleManaging();
  devicesPage.startCreateLab();
  devicesPage.onLabFormInput({ currentTarget: { dataset: { field: 'code' } }, detail: { value: 'lab-ui-01' } });
  devicesPage.onLabFormInput({ currentTarget: { dataset: { field: 'name' } }, detail: { value: '界面演示实验室' } });
  await devicesPage.saveLabForm();
  assert.equal(devicesPage.data.error, '');
  assert.ok(devicesPage.data.labs.some((lab) => lab.code === 'LAB-UI-01'));

  devicesPage.startCreateLab();
  devicesPage.onLabFormInput({ currentTarget: { dataset: { field: 'code' } }, detail: { value: 'LAB-UI-01' } });
  devicesPage.onLabFormInput({ currentTarget: { dataset: { field: 'name' } }, detail: { value: '重复编号实验室' } });
  await devicesPage.saveLabForm();
  assert.match(devicesPage.data.error, /已存在/);
  devicesPage.cancelLabForm();

  const newLabIndex = devicesPage.data.deviceLabOptions.findIndex((label) => label.includes('LAB-UI-01'));
  devicesPage.startCreateDevice();
  devicesPage.onDeviceLabChange({ detail: { value: String(newLabIndex) } });
  devicesPage.onDeviceFormInput({ currentTarget: { dataset: { field: 'code' } }, detail: { value: 'dev-ui-01' } });
  devicesPage.onDeviceFormInput({ currentTarget: { dataset: { field: 'name' } }, detail: { value: '界面演示设备' } });
  await devicesPage.saveDeviceForm();
  assert.equal(devicesPage.data.error, '');
  const created = devicesPage.data.devices.find((device) => device.code === 'DEV-UI-01');
  assert.ok(created);
  assert.equal(created.labText, '界面演示实验室');

  // device detail page: edit then disable and re-enable
  const devicePage = pageAt('../miniprogram/pages/device-detail/index.js');
  devicePage.onLoad({ id: encodeURIComponent(created.id) });
  await devicePage.load();
  devicePage.startEdit();
  devicePage.onEditInput({ currentTarget: { dataset: { field: 'name' } }, detail: { value: '界面演示设备改' } });
  await devicePage.saveEdit();
  assert.equal(devicePage.data.device.name, '界面演示设备改');
  await devicePage.toggleActive();
  assert.equal(devicePage.data.device.status, 'INACTIVE');
  await devicePage.toggleActive();
  assert.equal(devicePage.data.device.status, 'ACTIVE');

  // incident detail page: walk REPORTED -> ... -> CLOSED through the UI panel only
  const reported = unwrap(await core.execute({
    type: 'incident.report',
    requestId: 'ui2-report-1',
    payload: { deviceId: created.id, title: '界面闭环异常', description: '页面级状态机联调', severity: 'LOW' }
  })).incident;
  const detailPage = pageAt('../miniprogram/pages/incident-detail/index.js');
  detailPage.onLoad({ id: encodeURIComponent(reported.id) });
  await detailPage.load();
  assert.equal(detailPage.data.actions.length, 2);

  const walk = [
    'incident.confirm', 'incident.assign', 'incident.start', 'incident.submitReview', 'incident.close'
  ];
  for (const type of walk) {
    const labels = detailPage.data.actions.map((item) => item.type);
    assert.ok(labels.includes(type), `状态 ${detailPage.data.incident.status} 应提供 ${type}`);
    detailPage.selectAction({ currentTarget: { dataset: { type } } });
    assert.equal(detailPage.data.pendingType, type);
    detailPage.onReasonInput({ detail: { value: '页面联调说明' } });
    await detailPage.submitAction();
    assert.equal(detailPage.data.error, '');
  }
  assert.equal(detailPage.data.incident.status, 'CLOSED');
  assert.ok(detailPage.data.ended);
  assert.equal(detailPage.data.actions.length, 0);
  assert.equal(detailPage.data.events.length, 6);

  // workbench: status chips narrow the lists and the form submits one inspection
  const workbench = pageAt('../miniprogram/pages/workbench/index.js');
  await workbench.load();
  assert.equal(workbench.data.templates.length, 2);
  workbench.selectIncidentFilter({ currentTarget: { dataset: { value: 'CLOSED' } } });
  assert.ok(workbench.data.incidents.length > 0);
  assert.ok(workbench.data.incidents.every((incident) => incident.status === 'CLOSED'));
  workbench.selectInspectionFilter({ currentTarget: { dataset: { value: 'PLANNED' } } });
  assert.ok(workbench.data.inspections.every((task) => task.status === 'PLANNED'));

  workbench.toggleForm();
  assert.ok(workbench.data.formItems.length > 0);
  workbench.onItemPassedChange({ currentTarget: { dataset: { index: 0 } }, detail: { value: false } });
  workbench.onItemNoteInput({ currentTarget: { dataset: { index: 0 } }, detail: { value: '发现模拟磨损' } });
  await workbench.submitInspectionForm();
  assert.equal(workbench.data.error, '');
  assert.ok(toasts.includes('巡检结果已提交'));
  workbench.selectInspectionFilter({ currentTarget: { dataset: { value: 'SUBMITTED' } } });
  const submitted = workbench.data.inspections.find((task) => task.templateId === workbench.data.templates[0].id);
  assert.ok(submitted);
  assert.equal(submitted.results[0].passed, false);
  assert.equal(submitted.results[0].note, '发现模拟磨损');

  // 学生角色回到台账页：管理入口被页面门控隐藏，核心层对角色外命令兑底拒绝
  await core.execute({ type: 'demo.role.switch', payload: { role: 'student' } });
  await devicesPage.load();
  assert.equal(devicesPage.data.canManage, false);
  assert.equal(devicesPage.data.managing, false);
  devicesPage.toggleManaging();
  assert.match(devicesPage.data.error, /演示管理员/);
});

test('knowledge page manages documents as teacher and gates management for student', async () => {
  let counter = 0;
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-16T10:00:00+08:00',
    createId: () => `ui3-${++counter}`
  });
  global.getApp = () => ({ corePromise: Promise.resolve(core) });
  const toasts = [];
  global.wx = { showToast(options) { toasts.push(options && options.title); } };

  // 默认学生：资料管理卡不可见，导入入口被页面拦截
  const page = pageAt('../miniprogram/pages/knowledge/index.js');
  await page.load();
  assert.equal(page.data.canManage, false);
  page.startImport();
  assert.equal(page.data.docForm, null);

  // 教师：导入 -> 发布前检索不到 -> 发布后检索命中 v1
  await core.execute({ type: 'demo.role.switch', payload: { role: 'teacher' } });
  await page.load();
  assert.equal(page.data.canManage, true);
  page.startImport();
  assert.equal(page.data.docForm.mode, 'import');
  page.onDocFormInput({ currentTarget: { dataset: { field: 'title' } }, detail: { value: '界面演示规程' } });
  page.onDocFormInput({ currentTarget: { dataset: { field: 'section' } }, detail: { value: '操作要点' } });
  page.onDocFormInput({ currentTarget: { dataset: { field: 'text' } }, detail: { value: '通风柜面窗风速保持零点五米每秒' } });
  await page.saveDocForm();
  assert.equal(page.data.error, '');
  assert.ok(toasts.includes('资料已导入，待发布'));
  const importedDoc = page.data.documents.find((item) => item.title === '界面演示规程');
  assert.ok(importedDoc);
  assert.equal(importedDoc.versionText, '未发布');

  page.setData({ text: '面窗风速' });
  await page.search();
  assert.equal(page.data.noEvidence, true);

  await page.publishDoc({ currentTarget: { dataset: { id: importedDoc.id } } });
  assert.ok(toasts.includes('已发布，检索可用现行版本'));
  assert.equal(page.data.documents.find((item) => item.id === importedDoc.id).versionText, 'v1');

  page.setData({ text: '面窗风速' });
  await page.search();
  assert.ok(page.data.results.some((item) => item.sourceText.includes('v1') && item.sourceText.includes('界面演示规程')));

  // 修订 -> 发布 -> 检索命中 v2；草稿未发布前现行版本仍是 v1
  page.startRevise({ currentTarget: { dataset: { id: importedDoc.id } } });
  assert.equal(page.data.docForm.mode, 'revise');
  assert.equal(page.data.docForm.documentId, importedDoc.id);
  page.onDocFormInput({ currentTarget: { dataset: { field: 'section' } }, detail: { value: '操作要点' } });
  page.onDocFormInput({ currentTarget: { dataset: { field: 'text' } }, detail: { value: '修订后面窗风速保持零点六米每秒' } });
  await page.saveDocForm();
  assert.equal(page.data.error, '');
  assert.ok(toasts.includes('草稿已保存，待发布'));
  page.setData({ text: '面窗风速' });
  await page.search();
  assert.ok(page.data.results.every((item) => !item.sourceText.includes('零点六')));

  await page.publishDoc({ currentTarget: { dataset: { id: importedDoc.id } } });
  assert.equal(page.data.documents.find((item) => item.id === importedDoc.id).versionText, 'v2');
  page.setData({ text: '修订后' });
  await page.search();
  assert.ok(page.data.results.some((item) => item.sourceText.includes('v2') && item.sourceText.includes('界面演示规程')));
});

test('home export reports clipboard success only after the platform callback', async () => {
  const core = createLabSafeCore({ repository: new MemoryRepository({ seedFactory: createSeedSnapshot }) });
  global.getApp = () => ({ corePromise: Promise.resolve(core) });
  let clipboardOptions;
  let signalClipboard;
  const clipboardInvoked = new Promise((resolve) => { signalClipboard = resolve; });
  global.wx = {
    setClipboardData(options) {
      clipboardOptions = options;
      signalClipboard();
    }
  };

  const page = pageAt('../miniprogram/pages/home/index.js');
  const exportTask = page.exportBackup();
  await clipboardInvoked;
  assert.ok(page.data.backupText.length > 0);
  assert.match(page.data.copyMessage, /正在复制/);
  assert.doesNotMatch(page.data.copyMessage, /已复制/);

  clipboardOptions.success();
  await exportTask;
  assert.match(page.data.copyMessage, /已复制到剪贴板/);
  assert.equal(page.data.exporting, false);
});

test('home export keeps JSON available and reports clipboard failure', async () => {
  const core = createLabSafeCore({ repository: new MemoryRepository({ seedFactory: createSeedSnapshot }) });
  global.getApp = () => ({ corePromise: Promise.resolve(core) });
  let clipboardOptions;
  let signalClipboard;
  const clipboardInvoked = new Promise((resolve) => { signalClipboard = resolve; });
  global.wx = {
    setClipboardData(options) {
      clipboardOptions = options;
      signalClipboard();
    }
  };

  const page = pageAt('../miniprogram/pages/home/index.js');
  const exportTask = page.exportBackup();
  await clipboardInvoked;
  const exportedJson = page.data.backupText;
  clipboardOptions.fail({ errMsg: 'setClipboardData:fail' });
  await exportTask;
  assert.ok(exportedJson.length > 0);
  assert.equal(page.data.backupText, exportedJson);
  assert.match(page.data.copyMessage, /复制失败/);
  assert.equal(page.data.copyFailed, true);
  assert.equal(page.data.exporting, false);
});

test('home export clears prior copied status when a later export fails', async () => {
  let calls = 0;
  global.getApp = () => ({ corePromise: Promise.resolve({
    maintenance() {
      calls += 1;
      return calls === 1
        ? { ok: true, data: { backup: { kind: 'labsafe-demo-backup' }, byteSize: 42, warnings: [] } }
        : { ok: false, error: { code: 'STORAGE_ERROR', message: '第二次导出失败' } };
    }
  }) });
  global.wx = { setClipboardData({ success }) { success(); } };
  const page = pageAt('../miniprogram/pages/home/index.js');
  await page.exportBackup();
  assert.match(page.data.copyMessage, /已复制/);
  await page.exportBackup();
  assert.match(page.data.error, /第二次导出失败/);
  assert.equal(page.data.backupMeta, null);
  assert.equal(page.data.copyMessage, '');
});
