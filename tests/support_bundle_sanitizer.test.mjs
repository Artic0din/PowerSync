import assert from "node:assert/strict";
import test from "node:test";

import { SANITISED_MARKER, sanitizeSupportBundle } from "../docs/support-bundle/sanitizer.mjs";

test("credentials are removed while operational context remains", async () => {
  const result = await sanitizeSupportBundle(
    "2026-08-13T10:00:00 ERROR Authorization: Bearer secret-value request failed",
  );

  assert.ok(result.startsWith(SANITISED_MARKER));
  assert.doesNotMatch(result, /secret-value/);
  assert.match(result, /2026-08-13T10:00:00 ERROR/);
  assert.match(result, /request failed/);
});

test("identical identifiers receive stable placeholders", async () => {
  const result = await sanitizeSupportBundle(
    "user@example.com connected from 192.168.1.10; user@example.com retried from 192.168.1.10",
  );
  const emails = result.match(/\[EMAIL_[a-f0-9]+\]/g);
  const addresses = result.match(/\[IP_[a-f0-9]+\]/g);

  assert.equal(new Set(emails).size, 1);
  assert.equal(new Set(addresses).size, 1);
  assert.equal(emails.length, 2);
  assert.equal(addresses.length, 2);
});
