const test = require('node:test');
const assert = require('node:assert/strict');
const { createSeedSnapshot } = require('../miniprogram/core/seed');
const { createLabSafeCore } = require('../miniprogram/core/createLabSafeCore');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { unwrap } = require('../miniprogram/pages/_shared/ui');

async function coreWith(createId) {
  const core = createLabSafeCore({
    repository: new MemoryRepository({ seedFactory: createSeedSnapshot }),
    clock: () => '2026-10-16T10:00:00+08:00',
    createId
  });
  // 资料维护是教师/管理员的演示动作；本文件统一用教师角色。
  await core.execute({ type: 'demo.role.switch', payload: { role: 'teacher' } });
  return core;
}

test('knowledge import, revision and publication keep every citation resolvable', async () => {
  let counter = 0;
  const core = await coreWith(() => `kb-${++counter}`);

  // 草稿不进检索：导入后必须显式发布才可被引用
  const imported = unwrap(await core.execute({
    type: 'document.import', requestId: 'kb-import-1',
    payload: {
      title: '演示离心机规程',
      sections: [
        { section: '操作前', text: '离心机使用前需要确认转子安装牢固。' },
        { section: '操作中', text: '运行中禁止开启离心机盖子。' }
      ]
    }
  }));
  const draftSearch = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '转子' } }));
  assert.equal(draftSearch.status, 'NO_EVIDENCE');
  assert.deepEqual(draftSearch.items, []);

  unwrap(await core.execute({ type: 'document.publish', requestId: 'kb-publish-1', payload: { documentId: imported.document.id } }));
  const found = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '转子' } }));
  assert.equal(found.status, 'FOUND');
  const oldCitation = found.items[0];
  assert.equal(oldCitation.source.version, 'v1');
  assert.ok(oldCitation.source.documentTitle.includes('离心机'));
  assert.ok(oldCitation.source.section.length > 0);

  // 修订并发布 v2：检索只回现行版本
  unwrap(await core.execute({
    type: 'document.revise', requestId: 'kb-revise-1',
    payload: { documentId: imported.document.id, sections: [{ section: '操作前', text: '新版要求转子安装后二次确认并记录。' }] }
  }));
  unwrap(await core.execute({ type: 'document.publish', requestId: 'kb-publish-2', payload: { documentId: imported.document.id } }));
  const after = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '转子' } }));
  assert.equal(after.status, 'FOUND');
  assert.ok(after.items.every(item => item.text.includes('二次确认')));
  assert.equal(after.items[0].source.version, 'v2');

  // 旧引用在新版发布后仍可复核：原文与版本原样保留
  const review = unwrap(await core.query({ type: 'knowledge.citation', payload: { citationId: oldCitation.id } }));
  assert.equal(review.citation.text, oldCitation.text);
  assert.equal(review.citation.section, oldCitation.source.section);
  assert.equal(review.version.status, 'SUPERSEDED');
  assert.equal(review.isCurrent, false);

  // 每条现行引用都能回到存在的原文版本
  for (const item of after.items) {
    const check = unwrap(await core.query({ type: 'knowledge.citation', payload: { citationId: item.id } }));
    assert.equal(check.citation.text, item.text);
    assert.equal(check.isCurrent, true);
  }

  // 未命中的问题显式拒答，不生成答案
  const refused = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '量子纠缠' } }));
  assert.equal(refused.status, 'NO_EVIDENCE');
  assert.deepEqual(refused.items, []);

  // 未知引用显式返回空，不伪造原文
  const missing = unwrap(await core.query({ type: 'knowledge.citation', payload: { citationId: 'chunk-none' } }));
  assert.equal(missing.citation, null);

  // 无草稿时重复发布被拒绝
  const again = await core.execute({ type: 'document.publish', requestId: 'kb-publish-3', payload: { documentId: imported.document.id } });
  assert.equal(again.ok, false);
  assert.equal(again.error.code, 'NO_DRAFT');

  // 已有草稿时再次修订被拒绝，保证版本线清晰
  unwrap(await core.execute({
    type: 'document.revise', requestId: 'kb-revise-2',
    payload: { documentId: imported.document.id, sections: [{ section: '操作后', text: '用后清理转子并登记。' }] }
  }));
  const third = await core.execute({
    type: 'document.revise', requestId: 'kb-revise-3',
    payload: { documentId: imported.document.id, sections: [{ section: '操作后', text: '重复草稿。' }] }
  });
  assert.equal(third.ok, false);
  assert.equal(third.error.code, 'DRAFT_EXISTS');

  // 资料列表反映现行版本与草稿状态
  const list = unwrap(await core.query({ type: 'knowledge.document.list' }));
  const entry = list.items.find(item => item.id === imported.document.id);
  assert.equal(entry.currentVersion, 'v2');
  assert.equal(entry.hasDraft, true);
});

test('knowledge search filters expired versions and validates imports', async () => {
  let counter = 0;
  const core = await coreWith(() => `kbx-${++counter}`);

  const expired = unwrap(await core.execute({
    type: 'document.import', requestId: 'kbx-import-1',
    payload: {
      title: '已过期演示规程',
      effectiveUntil: '2026-10-10T00:00:00+08:00',
      sections: [{ section: '使用', text: '过期规程中的关键词应该被过滤。' }]
    }
  }));
  unwrap(await core.execute({ type: 'document.publish', requestId: 'kbx-publish-1', payload: { documentId: expired.document.id } }));

  // 过期过滤：现行但已过有效期的版本不参与检索
  const search = unwrap(await core.query({ type: 'knowledge.search', payload: { text: '关键词' } }));
  assert.equal(search.status, 'NO_EVIDENCE');

  // 引用复核仍能看到原文与过期状态
  const list = unwrap(await core.query({ type: 'knowledge.document.list' }));
  const entry = list.items.find(item => item.id === expired.document.id);
  assert.equal(entry.currentVersion, 'v1');
  assert.equal(entry.effectiveUntil, '2026-10-10T00:00:00+08:00');

  // 非法导入被拒绝
  const bad = await core.execute({
    type: 'document.import', requestId: 'kbx-import-2',
    payload: { title: '坏数据', sections: [{ section: '', text: '章节名不能为空。' }] }
  });
  assert.equal(bad.ok, false);
  assert.equal(bad.error.code, 'INVALID_INPUT');
  const badExpiry = await core.execute({
    type: 'document.import', requestId: 'kbx-import-3',
    payload: { title: '坏日期规程', effectiveUntil: '不是日期', sections: [{ section: '使用', text: '内容。' }] }
  });
  assert.equal(badExpiry.ok, false);
  assert.equal(badExpiry.error.code, 'INVALID_INPUT');
});
