// Run with: node --test react-app/tests
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fingerprint, mergeConversations } from '../src/lib/convSync.js';

const conv = (id, text, extra = {}) => ({
  id,
  title: id,
  createdAt: 1,
  messages: [{ role: 'user', content: text }],
  ...extra,
});

test('fingerprint ignores UI-only fields and changes with content', () => {
  const a = conv('a', 'salam');
  assert.equal(fingerprint(a), fingerprint({ ...a, updatedAt: 99 }));
  assert.equal(
    fingerprint({ ...a, messages: [{ ...a.messages[0], statusText: 'typing' }] }),
    fingerprint(a)
  );
  assert.notEqual(fingerprint(a), fingerprint(conv('a', 'salam!')));
});

test('server copy replaces an unedited local copy', () => {
  const local = conv('a', 'old');
  const remote = conv('a', 'edited on phone', { updatedAt: 50 });
  const { merged, synced } = mergeConversations([local], { conversations: [remote], deleted: [] }, { a: fingerprint(local) });
  assert.equal(merged[0].messages[0].content, 'edited on phone');
  assert.equal(synced.a, fingerprint(remote));
});

test('local edits made since the last sync win', () => {
  const synced = { a: fingerprint(conv('a', 'old')) };
  const local = conv('a', 'edited here offline');
  const remote = conv('a', 'old', { updatedAt: 50 });
  const { merged, synced: next } = mergeConversations([local], { conversations: [remote], deleted: [] }, synced);
  assert.equal(merged[0].messages[0].content, 'edited here offline');
  assert.notEqual(next.a, fingerprint(local)); // still to upload
});

test('conversations from other devices are added, deleted ones removed', () => {
  const kept = conv('kept', 'x');
  const gone = conv('gone', 'y');
  const synced = { kept: fingerprint(kept), gone: fingerprint(gone) };
  const remote = { conversations: [kept, conv('new', 'from phone', { updatedAt: 10 })], deleted: ['gone'] };
  const { merged, synced: next } = mergeConversations([kept, gone], remote, synced);
  assert.deepEqual(merged.map((c) => c.id).sort(), ['kept', 'new']);
  assert.equal(next.gone, undefined);
  assert.ok(next.new);
});

test('a deleted conversation edited here since the last sync is kept', () => {
  const local = conv('a', 'new text');
  const { merged } = mergeConversations([local], { conversations: [], deleted: ['a'] }, { a: 'old-fingerprint' });
  assert.equal(merged.length, 1);
});

test('empty drafts stay on top, the rest newest first', () => {
  const draft = { id: 'draft', title: 'Nouvelle discussion', createdAt: 1, messages: [] };
  const remote = {
    conversations: [conv('old', 'a', { updatedAt: 10 }), conv('recent', 'b', { updatedAt: 20 })],
    deleted: [],
  };
  const { merged } = mergeConversations([draft], remote, {});
  assert.deepEqual(merged.map((c) => c.id), ['draft', 'recent', 'old']);
});

test('large photo previews are not uploaded', () => {
  const big = 'data:image/png;base64,' + 'A'.repeat(200_000);
  const withPhoto = {
    id: 'p',
    title: 'p',
    messages: [{ role: 'user', content: 'chouf', attachments: [{ filename: 'x.png', data_url: big }] }],
  };
  const withoutPhoto = {
    id: 'p',
    title: 'p',
    messages: [{ role: 'user', content: 'chouf', attachments: [{ filename: 'x.png' }] }],
  };
  assert.equal(fingerprint(withPhoto), fingerprint(withoutPhoto));
});
