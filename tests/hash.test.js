const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { sha256, canonicalStringify } = require('../miniprogram/core/hash');

test('pure JS SHA-256 matches platform crypto for ASCII and Chinese', () => {
  for (const value of ['', 'abc', '模拟异常上报', '😀 LabSafe']) {
    assert.equal(sha256(value), crypto.createHash('sha256').update(value, 'utf8').digest('hex'));
  }
});

test('canonical JSON sorts object keys recursively', () => {
  assert.equal(canonicalStringify({ z: 1, a: { y: 2, x: 3 } }), '{"a":{"x":3,"y":2},"z":1}');
});
