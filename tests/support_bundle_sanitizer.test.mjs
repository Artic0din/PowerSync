import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_FILE_BYTES,
  SANITISED_MARKER,
  decodeUtf8SupportFile,
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
  const result = await sanitizeSupportBundle(
    '{"password": "correct horse battery staple"}',
  );

  assert.doesNotMatch(result, /correct|horse|battery|staple/);
  assert.match(result, /"password": "\[REDACTED\]"/);
});

test("escaped quotes cannot expose a credential suffix", async () => {
  const result = await sanitizeSupportBundle(
    String.raw`{"password":"abc\"def ghi","next":"preserved"}`,
  );

  assert.doesNotMatch(result, /abc|def ghi/);
  assert.match(result, /"next":"preserved"/);
});

test("unquoted credentials consume the complete line", async () => {
  const result = await sanitizeSupportBundle(
    "password: correct horse battery staple\nnext: preserved",
  );

  assert.doesNotMatch(result, /correct|horse|battery|staple/);
  assert.match(result, /next: preserved/);
});

test("fine-grained GitHub tokens are removed", async () => {
  const result = await sanitizeSupportBundle(
    "github_pat_abcdefghijklmnopqrstuvwxyz0123456789",
  );

  assert.doesNotMatch(result, /github_pat_/);
});

test("standalone PowerSync and explanation credentials are removed", async () => {
  const result = await sanitizeSupportBundle(
    `psk_abcdefghijklmnopqrstuvwxyz psync_${"a".repeat(43)} xai-abcdefghijklmnopqrstuvwxyz AIzaabcdefghijklmnopqrstuvwxyz`,
  );

  assert.doesNotMatch(result, /psk_|psync_|xai-|AIza/);
});

test("namespaced integration credentials are removed", async () => {
  const result = await sanitizeSupportBundle(
    '{"alphaess_cloud_app_secret":"alpha-secret","sigenergy_pass_enc":"encoded-pass","teslemetry_api_token":"tesla-token","timezone_token":"AEST"}',
  );

  assert.doesNotMatch(result, /alpha-secret|encoded-pass|tesla-token/);
  assert.match(result, /"timezone_token":"AEST"/);
});

test("all repository credential key suffixes are removed", async () => {
  const result = await sanitizeSupportBundle(
    '{"foxess_cloud_password":"foxess-secret","enphase_password":"enphase-secret","openweathermap_api_key":"weather-secret","powerwall_local_private_key_pem":"private-material"}',
  );

  assert.doesNotMatch(
    result,
    /foxess-secret|enphase-secret|weather-secret|private-material/,
  );
});

test("quoted JSON authentication headers are removed", async () => {
  const result = await sanitizeSupportBundle(
    '{"Authorization":"Bearer super-secret-token","Set-Cookie":"session=customer-secret"}',
  );

  assert.doesNotMatch(result, /super-secret-token|customer-secret/);
});

test("every authorization header value is removed", async () => {
  const result = await sanitizeSupportBundle(
    '{"Authorization":"apikey customer-secret","next":"preserved"}\nAuthorization: raw-token-value',
  );

  assert.doesNotMatch(result, /customer-secret|raw-token-value/);
  assert.match(result, /"next":"preserved"/);
});

test("camelCase credentials are removed", async () => {
  const result = await sanitizeSupportBundle(
    '{"accessToken":"access-secret","refreshToken":"refresh-secret"}',
  );

  assert.doesNotMatch(result, /access-secret|refresh-secret/);
});

test("timezone token metadata remains visible", async () => {
  const result = await sanitizeSupportBundle('{"timezone_token":"AEST"}');

  assert.match(result, /"timezone_token":"AEST"/);
});

test("unquoted JSON primitives do not consume later fields", async () => {
  const result = await sanitizeSupportBundle(
    '{"token":null,"error":"connection refused","status":500}',
  );

  assert.match(result, /"error":"connection refused"/);
  assert.match(result, /"status":500/);
});

test("quoted PowerSync identifiers are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    '{"serial_number": "TG123456789", "serialNumber": "TG123456789", "username": "alice"}',
  );

  assert.doesNotMatch(result, /TG123456789|alice/);
  assert.equal((result.match(/\[SERIAL_1\]/g) ?? []).length, 2);
  assert.match(result, /\[USER_1\]/);
});

test("PowerSync site and gateway identifiers are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    '{"gateway_id":"gateway-0123456789abcdef","asset_site_id":"12345678-1234-1234-1234-123456789abc","site_id":"01KAR0YMB7JQDVZ10SN1SGA0CV","din":"DIN0123456789ABC"}',
  );

  assert.doesNotMatch(
    result,
    /gateway-0123456789abcdef|12345678-1234-1234-1234-123456789abc|01KAR0YMB7JQDVZ10SN1SGA0CV|DIN0123456789ABC/,
  );
  assert.equal((result.match(/\[DEVICE_\d+\]/g) ?? []).length, 4);
});

test("namespaced and customer identifiers are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    '{"amber_site_id":"amber-site","tesla_energy_site_id":"tesla-site","sigenergy_device_id":"sig-device","accountNumber":"customer-account","siteAddress":"1 Main Street"}',
  );

  assert.doesNotMatch(
    result,
    /amber-site|tesla-site|sig-device|customer-account|1 Main Street/,
  );
  assert.equal((result.match(/\[(?:USER|DEVICE)_\d+\]/g) ?? []).length, 5);
});

test("repository sensitive identifier keys are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    '{"nmi":"E1234567890","accountName":"Alice Smith","siteIdentifier":"site-secret","device_sn":"device-secret"}',
  );

  assert.doesNotMatch(
    result,
    /E1234567890|Alice Smith|site-secret|device-secret/,
  );
});

test("dotted MAC addresses are pseudonymised", async () => {
  const result = await sanitizeSupportBundle("adapter aabb.ccdd.eeff disconnected");

  assert.doesNotMatch(result, /aabb\.ccdd\.eeff/i);
  assert.match(result, /\[MAC_1\]/);
});

test("PowerSync identifiers in log phrases and URLs are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    "request for site 01KAR0YMB7JQDVZ10SN1SGA0CV energy_sites/1234567890123",
  );

  assert.doesNotMatch(result, /01KAR0YMB7JQDVZ10SN1SGA0CV|1234567890123/);
  assert.equal((result.match(/\[DEVICE_\d+\]/g) ?? []).length, 2);
});

test("multiword usernames are pseudonymised completely", async () => {
  const result = await sanitizeSupportBundle('{"username": "Alice Smith"}');

  assert.doesNotMatch(result, /Alice|Smith/);
  assert.match(result, /"username": "\[USER_1\]"/);
});

test("lowercase VINs are pseudonymised", async () => {
  const result = await sanitizeSupportBundle("vin 5yj3e1ea7nf0000a1");

  assert.doesNotMatch(result, /5yj3e1ea7nf0000a1/i);
  assert.match(result, /\[VIN_1\]/);
});

test("numeric timestamps are not classified as VINs", async () => {
  const result = await sanitizeSupportBundle("event 20260813123456789");

  assert.match(result, /20260813123456789/);
  assert.doesNotMatch(result, /\[VIN_/);
});

test("invalid UTF-8 input is rejected", () => {
  assert.throws(
    () => decodeUtf8SupportFile(Uint8Array.from([0xc3, 0x28])),
    /encoded data was not valid|invalid/i,
  );
});

test("four-part software versions remain visible", async () => {
  const result = await sanitizeSupportBundle("integration version 1.2.3.4");

  assert.match(result, /integration version 1\.2\.3\.4/);
  assert.doesNotMatch(result, /\[IP_/);
});

test("an address after a bare integration label is pseudonymised", async () => {
  const result = await sanitizeSupportBundle("integration 192.168.1.10 failed");

  assert.doesNotMatch(result, /192\.168\.1\.10/);
  assert.match(result, /\[IP_1\]/);
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

test("Windows profile names containing spaces are fully pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    String.raw`C:\Users\Alice Smith\AppData\powersync.log`,
  );

  assert.doesNotMatch(result, /Alice|Smith/);
  assert.match(result, /\[HOME_1\]\\AppData/);
});

test("Unix and Windows home paths receive distinct stable placeholders", async () => {
  const result = await sanitizeSupportBundle(
    String.raw`/home/alice/powersync.log C:\Users\bob\powersync.log`,
  );

  assert.match(result, /\[HOME_1\]/);
  assert.match(result, /\[HOME_2\]/);
});

test("IPv6 addresses are pseudonymised", async () => {
  const result = await sanitizeSupportBundle(
    "Connected to 2001:db8:85a3::8a2e:370:7334",
  );

  assert.match(result, /\[IPV6_1\]/);
  assert.doesNotMatch(result, /2001:db8/);
});

test("IPv4-embedded IPv6 addresses are pseudonymised as one value", async () => {
  const result = await sanitizeSupportBundle("Connected to ::ffff:192.168.1.10");

  assert.match(result, /\[IPV6_1\]/);
  assert.doesNotMatch(result, /192\.168\.1\.10|\.168\.1\.10/);
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
