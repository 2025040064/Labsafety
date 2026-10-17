const { SCHEMA_VERSION, createSeedSnapshot } = require('../core/seed');
const { canonicalStringify } = require('../core/hash');

function repositoryError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function copySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot) || snapshot.schemaVersion !== SCHEMA_VERSION) {
    throw repositoryError('INVALID_SNAPSHOT', `Expected schemaVersion ${SCHEMA_VERSION}`);
  }
  try {
    const copy = JSON.parse(canonicalStringify(snapshot));
    if (!copy || copy.schemaVersion !== SCHEMA_VERSION) throw new Error('Invalid JSON snapshot');
    return copy;
  } catch (cause) {
    throw repositoryError('INVALID_SNAPSHOT', `Snapshot is not canonical JSON: ${cause.message}`);
  }
}

/** In-memory implementation of the same optimistic-concurrency contract as WxLocalRepository. */
class MemoryRepository {
  constructor({ seedFactory = createSeedSnapshot } = {}) {
    this.generation = 0;
    this.snapshot = copySnapshot(seedFactory());
  }

  async load() {
    return { generation: this.generation, snapshot: copySnapshot(this.snapshot) };
  }

  async commit(expectedGeneration, nextSnapshot) {
    if (!Number.isSafeInteger(expectedGeneration) || expectedGeneration !== this.generation) {
      throw repositoryError('GENERATION_CONFLICT', 'Snapshot changed before commit');
    }
    const copy = copySnapshot(nextSnapshot);
    this.snapshot = copy;
    this.generation += 1;
    return { generation: this.generation };
  }
}

module.exports = { MemoryRepository };
