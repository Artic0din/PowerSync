import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_FILE_BYTES,
  SANITISED_MARKER,
  sanitizeSupportBundle,
} from "../docs/support-bundle/sanitizer.mjs";

test("credentials are removed while operational context remains", async () => {
  const result = await sanitizeSupportBundle(
    "2026-08-13T10:00:00 ERROR Authorization: Bearer secret-value request failed",
  );

  assert.ok(result.startsWith(SANITISED_MARKER));
  assert.doesNotMatch(result, /secret-value/);
  assert.match(result, /2026-08-13T10:00:00 ERROR/);
  assert.match(result, /request failed/);
});

test("cookies and generic tokens are removed", async () => {
  const result = await sanitizeSupportBundle(
    "Cookie: session=customer-session-value\nrefresh_token=customer-refresh-token-value",
  );

  assert.doesNotMatch(result, /customer-session-value/);
  assert.doesNotMatch(result, /customer-refresh-token-value/);
});

test("quoted JSON credentials are removed", async () => {
  const result = await sanitizeSupportBundle('{"password": "super-secret-value"}');

  assert.doesNotMatch(result, /super-secret-value/);
});

test("identical identifiers receive stable placeholders", async () => {
  const result = await sanitizeSupportBundle(
    "user@example.com connected from 192.168.1.10; user@example.com retried from 192.168.1.10",
  );
  const emails = result.match(/\[EMAIL_\d+\]/g);
  const addresses = result.match(/\[IP_\d+\]/g);

  assert.equal(new Set(emails).size, 1);
  assert.equal(new Set(addresses).size, 1);
  assert.equal(emails.length, 2);
  assert.equal(addresses.length, 2);
});

test("Windows home paths are pseudonymised consistently", async () => {
  const result = await sanitizeSupportBundle(
    String.raw`C:\Users\ryan\powersync\log.txt C:\Users\ryan\powersync\config.json`,
  );
  const homes = result.match(/\[HOME_\d+\]/g);

  assert.equal(new Set(homes).size, 1);
  assert.equal(homes.length, 2);
  assert.doesNotMatch(result, /C:\\Users\\ryan/);
});

test("opaque identifiers cannot be reversed from a deterministic digest", async () => {
  const result = await sanitizeSupportBundle("Connected from 192.168.1.10");

  assert.match(result, /\[IP_1\]/);
  assert.doesNotMatch(result, /805ebf201c/);
});

test("sanitised output cannot exceed the upload limit", async () => {
  const input = "a".repeat(MAX_FILE_BYTES);

  await assert.rejects(
    sanitizeSupportBundle(input),
    /Sanitised bundle exceeds the 512 KB limit/,
  );
});
