const test = require('node:test');
const assert = require('node:assert/strict');
const { MemoryRepository } = require('../miniprogram/infrastructure/MemoryRepository');
const { WxLocalRepository, KEYS, MAX_RECORD_BYTES } = require('../miniprogram/infrastructure/WxLocalRepository');
const { createSeedSnapshot } = require('../miniprogram/core/seed');

function fakeStorage() {
  const values = new Map();
  return {
    values,
    onSet: null,
    getStorageSync(key) { return values.has(key) ? values.get(key) : ''; },
    setStorageSync(key, value) {
      if (this.onSet) return this.onSet(key, value, values);
      values.set(key, value);
    }
  };
}

async function nextSnapshot(repository, marker) {
  const current = await repository.load();
  current.snapshot.marker = marker;
  await repository.commit(current.generation, current.snapshot);
}

test('MemoryRepository returns copies and rejects stale generations', async () => {
  const repository = new MemoryRepository({ seedFactory: createSeedSnapshot });
  const first = await repository.load();
  first.snapshot.devices[0].name = 'changed only in caller';
  assert.notEqual((await repository.load()).snapshot.devices[0].name, first.snapshot.devices[0].name);
  const result = await repository.commit(0, first.snapshot);
  assert.equal(result.generation, 1);
  await assert.rejects(repository.commit(0, first.snapshot), error => error.code === 'GENERATION_CONFLICT');
});

test('WxLocalRepository seeds without writing and commits through A/B pointer', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage, seedFactory: createSeedSnapshot });
  const first = await repository.load();
  assert.equal(first.generation, 0);
  assert.equal(storage.values.size, 0);
  await nextSnapshot(repository, 'first');
  assert.equal(JSON.parse(storage.values.get(KEYS.pointer)).slot, 'A');
  await nextSnapshot(repository, 'second');
  assert.equal(JSON.parse(storage.values.get(KEYS.pointer)).slot, 'B');
  assert.equal((await repository.load()).snapshot.marker, 'second');
  await assert.rejects(repository.commit(1, first.snapshot), error => error.code === 'GENERATION_CONFLICT');
});

test('Prepared newer slot remains invisible when pointer write fails', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage });
  await nextSnapshot(repository, 'first');
  await nextSnapshot(repository, 'second');
  storage.onSet = (key, value, values) => {
    if (key === KEYS.pointer) throw new Error('simulated interrupted pointer write');
    values.set(key, value);
  };
  const { generation, snapshot } = await repository.load();
  snapshot.marker = 'uncommitted third';
  await assert.rejects(repository.commit(generation, snapshot), error => error.code === 'STORAGE_WRITE_FAILED');
  assert.equal(JSON.parse(storage.values.get(KEYS.A)).generation, 3);
  assert.equal((await repository.load()).generation, 2);
  assert.equal((await repository.load()).snapshot.marker, 'second');
});

test('Missing pointer with two valid slots selects the older generation', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage });
  await nextSnapshot(repository, 'first');
  await nextSnapshot(repository, 'second');
  storage.values.delete(KEYS.pointer);
  const recovered = await repository.load();
  assert.equal(recovered.generation, 1);
  assert.equal(recovered.snapshot.marker, 'first');
});

test('Missing pointer with one slot is ambiguous, and fully corrupt storage is explicit', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage });
  await nextSnapshot(repository, 'first');
  storage.values.delete(KEYS.pointer);
  await assert.rejects(repository.load(), error => error.code === 'STORAGE_UNCERTAIN');
  storage.values.set(KEYS.A, 'not JSON');
  await assert.rejects(repository.load(), error => error.code === 'STORAGE_CORRUPT');
});

test('Failed readback never advances pointer', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage });
  await nextSnapshot(repository, 'first');
  await nextSnapshot(repository, 'second');
  storage.onSet = (key, value, values) => values.set(key, key === KEYS.A ? 'truncated' : value);
  const { generation, snapshot } = await repository.load();
  snapshot.marker = 'third';
  await assert.rejects(repository.commit(generation, snapshot), error => error.code === 'STORAGE_VERIFY_FAILED');
  assert.equal((await repository.load()).generation, 2);
});

test('Storage size is checked in UTF-8 bytes before writing', async () => {
  const storage = fakeStorage();
  const repository = new WxLocalRepository({ storage });
  const { snapshot } = await repository.load();
  snapshot.largeText = '汉'.repeat(Math.ceil(MAX_RECORD_BYTES / 3));
  await assert.rejects(repository.commit(0, snapshot), error => error.code === 'STORAGE_LIMIT');
  assert.equal(storage.values.size, 0);
});
