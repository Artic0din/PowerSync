export const SANITISED_MARKER = "PowerSync sanitised support bundle v1";
export const MAX_FILE_BYTES = 512 * 1024;

const SECRET_PATTERNS = [
  [/authorization\s*:\s*(?:bearer|basic)\s+\S+/gi, "Authorization: [REDACTED]"],
  [/(?:set-cookie|cookie)\s*:\s*[^\r\n]+/gi, "Cookie: [REDACTED]"],
  [
    /(["']?(?:password|passwd|token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|cookie|api[_ -]?key|client[_ -]?secret)["']?\s*[:=]\s*)["']?[^\s,"'}]+["']?/gi,
    '$1"[REDACTED]"',
  ],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, "[REDACTED_GITHUB_TOKEN]"],
  [/\bAKIA[0-9A-Z]{16}\b/g, "[REDACTED_AWS_KEY]"],
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[REDACTED_JWT]"],
  [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, "[REDACTED_PRIVATE_KEY]"],
  [/https:\/\/(?:discord(?:app)?\.com\/api\/webhooks|hooks\.slack\.com\/services)\/\S+/gi, "[REDACTED_WEBHOOK]"],
];

const IDENTIFIER_PATTERNS = [
  ["EMAIL", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
  ["IP", /\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b/g],
  ["MAC", /\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b/gi],
  ["VIN", /\b[A-HJ-NPR-Z0-9]{17}\b/g],
  ["HOME", /\/(?:Users|home)\/[^/\s]+/g],
  ["HOME", /[A-Z]:\\Users\\[^\\\s]+/gi],
  ["SERIAL", /(?<=(?:serial|device[_ -]?id)\s*[:=]\s*)[A-Za-z0-9_-]{5,}/gi],
  ["USER", /(?<=(?:user(?:name)?|login)\s*[:=]\s*)[A-Za-z0-9_.@-]{3,}/gi],
];

async function pseudonymise(text, label, pattern) {
  const values = [...new Set(text.match(pattern) ?? [])];
  const replacements = new Map();
  for (const [index, value] of values.entries()) {
    replacements.set(value, `[${label}_${index + 1}]`);
  }
  return text.replace(pattern, (value) => replacements.get(value) ?? `[${label}]`);
}

export async function sanitizeSupportBundle(input) {
  let output = input.replaceAll("\0", "");
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    output = output.replace(pattern, replacement);
  }
  for (const [label, pattern] of IDENTIFIER_PATTERNS) {
    output = await pseudonymise(output, label, pattern);
  }
  const bundle = `${SANITISED_MARKER}\n${output}`;
  if (new TextEncoder().encode(bundle).byteLength > MAX_FILE_BYTES) {
    throw new RangeError("Sanitised bundle exceeds the 512 KB limit");
  }
  return bundle;
}

function downloadBundle(content) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  link.download = "powersync-sanitised-support.log";
  link.click();
  URL.revokeObjectURL(link.href);
}

if (typeof document !== "undefined") {
  const fileInput = document.querySelector("#log-file");
  const createButton = document.querySelector("#create-bundle");
  const status = document.querySelector("#status");

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    createButton.disabled = !file || file.size > MAX_FILE_BYTES;
    status.textContent = file?.size > MAX_FILE_BYTES
      ? "That file exceeds the 512 KB limit. Export a smaller relevant log window."
      : file
        ? "Ready to create a local sanitised copy."
        : "Choose one text log up to 512 KB.";
  });

  createButton.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file || file.size > MAX_FILE_BYTES) return;
    createButton.disabled = true;
    status.textContent = "Sanitising locally…";
    try {
      const content = await sanitizeSupportBundle(await file.text());
      downloadBundle(content);
      status.textContent = "Bundle created. Review the downloaded file before uploading it.";
    } catch (error) {
      status.textContent = error instanceof RangeError
        ? "The sanitised bundle exceeds 512 KB. Export a smaller relevant log window."
        : "The bundle could not be created. Use a UTF-8 text export and try again.";
    } finally {
      createButton.disabled = false;
    }
  });
}
