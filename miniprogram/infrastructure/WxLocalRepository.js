const { canonicalStringify, sha256 } = require('../core/hash');
const { SCHEMA_VERSION, createSeedSnapshot } = require('../core/seed');

const KEYS = Object.freeze({
  A: 'labsafe.v1.snapshot.A',
  B: 'labsafe.v1.snapshot.B',
  pointer: 'labsafe.v1.snapshot.active'
});
// WeChat's single-key limit is 1 MB. Leave headroom for metadata and runtime differences.
const MAX_RECORD_BYTES = 900 * 1024;

// 读旧写新：仓库接缝同时接受上一版与当前版快照，读取阶段只验证不做升级；
// schema 升级由核心层在迁移成功后的下一次提交自然完成。
const SUPPORTED_SCHEMA_VERSIONS = Object.freeze([1, SCHEMA_VERSION]);

function repositoryError(code, message, cause) {
  const error = new Error(message);
  error.code = code;
  if (cause) error.cause = cause;
  return error;
}

function utf8ByteLength(input) {
  let size = 0;
  for (const character of input) {
    const code = character.codePointAt(0);
    size += code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
  }
  return size;
}

function normalizeSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)
    || !SUPPORTED_SCHEMA_VERSIONS.includes(snapshot.schemaVersion)) {
    throw repositoryError('INVALID_SNAPSHOT', `Expected schemaVersion in ${SUPPORTED_SCHEMA_VERSIONS.join('/')}`);
  }
  try {
    // Parsing the canonical form rejects circular references and unsupported values.
    const canonical = canonicalStringify(snapshot);
    const normalized = JSON.parse(canonical);
    return { snapshot: normalized, checksum: sha256(canonical) };
  } catch (cause) {
    throw repositoryError('INVALID_SNAPSHOT', `Snapshot is not canonical JSON: ${cause.message}`, cause);
  }
}

/**
 * The exact slot record a commit would write, serialized once so the core's backup
 * precheck and this repository's commit share a single capacity judgment.
 */
function buildSlotRecord(generation, snapshot) {
  const normalized = normalizeSnapshot(snapshot);
  const record = { generation, checksum: normalized.checksum, snapshot: normalized.snapshot };
  const serialized = JSON.stringify(record);
  return { record, serialized, byteLength: utf8ByteLength(serialized) };
}

/** UTF-8 byte size of the slot record that would be written for this snapshot. */
function slotRecordBytes(generation, snapshot) {
  return buildSlotRecord(generation, snapshot).byteLength;
}

function parseRecord(raw, slot) {
  if (raw === '' || raw === undefined || raw === null) return { slot, present: false, valid: false };
  try {
    const record = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!record || !Number.isSafeInteger(record.generation) || record.generation < 1 ||
        typeof record.checksum !== 'string' || !/^[a-f0-9]{64}$/.test(record.checksum)) {
      return { slot, present: true, valid: false };
    }
    const normalized = normalizeSnapshot(record.snapshot);
    if (record.checksum !== normalized.checksum) return { slot, present: true, valid: false };
    return { slot, present: true, valid: true, generation: record.generation,
      checksum: record.checksum, snapshot: normalized.snapshot };
  } catch (_) {
    return { slot, present: true, valid: false };
  }
}

function parsePointer(raw) {
  if (raw === '' || raw === undefined || raw === null) return { present: false, valid: false };
  try {
    const pointer = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!pointer || !['A', 'B'].includes(pointer.slot) || !Number.isSafeInteger(pointer.generation) ||
        pointer.generation < 1 || typeof pointer.checksum !== 'string' || !/^[a-f0-9]{64}$/.test(pointer.checksum)) {
      return { present: true, valid: false };
    }
    return { present: true, valid: true, slot: pointer.slot,
      generation: pointer.generation, checksum: pointer.checksum };
  } catch (_) {
    return { present: true, valid: false };
  }
}

/**
 * Local WeChat storage with two checksummed slots and a last-written active pointer.
 * A prepared inactive slot is never visible while the existing pointer remains valid.
 */
class WxLocalRepository {
  constructor({ storage, seedFactory = createSeedSnapshot } = {}) {
    if (!storage || typeof storage.getStorageSync !== 'function' || typeof storage.setStorageSync !== 'function') {
      throw repositoryError('INVALID_STORAGE', 'Expected WeChat synchronous storage API');
    }
    this.storage = storage;
    this.seedFactory = seedFactory;
  }

  _get(key) {
    try { return this.storage.getStorageSync(key); }
    catch (cause) { throw repositoryError('STORAGE_READ_FAILED', `Could not read ${key}`, cause); }
  }

  _set(key, value) {
    try { this.storage.setStorageSync(key, value); }
    catch (cause) { throw repositoryError('STORAGE_WRITE_FAILED', `Could not write ${key}`, cause); }
  }

  _readState() {
    const a = parseRecord(this._get(KEYS.A), 'A');
    const b = parseRecord(this._get(KEYS.B), 'B');
    const pointer = parsePointer(this._get(KEYS.pointer));
    const records = [a, b];
    if (!a.present && !b.present && !pointer.present) {
      return { slot: null, generation: 0, snapshot: normalizeSnapshot(this.seedFactory()).snapshot };
    }

    if (pointer.valid) {
      const pointed = records.find(record => record.slot === pointer.slot && record.valid &&
        record.generation === pointer.generation && record.checksum === pointer.checksum);
      if (pointed) return pointed;
    }

    const valid = records.filter(record => record.valid);
    if (valid.length === 2) {
      // A pointer may be damaged. Favor the older complete generation: the higher
      // one could be a prepared slot whose pointer write never succeeded.
      return valid[0].generation <= valid[1].generation ? valid[0] : valid[1];
    }
    if (valid.length === 1) {
      // This could be either a committed slot whose pointer was lost or an
      // uncommitted first write. Never guess which one is authoritative.
      throw repositoryError('STORAGE_UNCERTAIN', 'One snapshot exists without a valid active pointer');
    }
    throw repositoryError('STORAGE_CORRUPT', 'No recoverable committed snapshot');
  }

  async load() {
    const state = this._readState();
    return { generation: state.generation, snapshot: normalizeSnapshot(state.snapshot).snapshot };
  }

  async commit(expectedGeneration, nextSnapshot) {
    const current = this._readState();
    if (!Number.isSafeInteger(expectedGeneration) || expectedGeneration !== current.generation) {
      throw repositoryError('GENERATION_CONFLICT', 'Snapshot changed before commit');
    }
    const slot = current.slot === 'A' ? 'B' : 'A';
    const generation = current.generation + 1;
    const { record, serialized, byteLength } = buildSlotRecord(generation, nextSnapshot);
    if (byteLength > MAX_RECORD_BYTES) {
      throw repositoryError('STORAGE_LIMIT', `Snapshot exceeds ${MAX_RECORD_BYTES} UTF-8 bytes`);
    }
    this._set(KEYS[slot], serialized);
    const verified = parseRecord(this._get(KEYS[slot]), slot);
    if (!verified.valid || verified.generation !== generation || verified.checksum !== record.checksum) {
      throw repositoryError('STORAGE_VERIFY_FAILED', 'Written snapshot did not verify');
    }
    const pointer = JSON.stringify({ slot, generation, checksum: record.checksum });
    this._set(KEYS.pointer, pointer);
    return { generation };
  }
}

module.exports = { WxLocalRepository, KEYS, MAX_RECORD_BYTES, slotRecordBytes };
