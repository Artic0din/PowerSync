export const SANITISED_MARKER = "PowerSync sanitised support bundle v1";
export const MAX_FILE_BYTES = 512 * 1024;

const SECRET_PATTERNS = [
  [/["']?authorization["']?\s*:\s*["']?(?:bearer|basic)\s+[^"'\s,}]+["']?/gi, "Authorization: [REDACTED]"],
  [/["']?(?:set-cookie|cookie)["']?\s*:\s*(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\r\n]+)/gi, "Cookie: [REDACTED]"],
  [
    /((?<![A-Z0-9_-])["']?(?:alphaess[_ -]?cloud[_ -]?app[_ -]?secret|sigenergy[_ -]?pass[_ -]?enc|teslemetry[_ -]?api[_ -]?token|password|passwd|token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|cookie|api[_ -]?key|client[_ -]?secret)["']?\s*[:=]\s*)(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]*)/gi,
    '$1"[REDACTED]"',
  ],
  [/\bpsk_[A-Za-z0-9]{20,}\b/gi, "[REDACTED_POWERSYNC_TOKEN]"],
  [/\bpsync_[A-Za-z0-9_-]{43}\b/g, "[REDACTED_POWERSYNC_TOKEN]"],
  [/\bxai-[A-Za-z0-9_-]{20,}\b/g, "[REDACTED_XAI_KEY]"],
  [/\bAIza[A-Za-z0-9_-]{20,}\b/g, "[REDACTED_GEMINI_KEY]"],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, "[REDACTED_GITHUB_TOKEN]"],
  [/\bgithub_pat_[A-Za-z0-9_]{20,}\b/g, "[REDACTED_GITHUB_TOKEN]"],
  [/\bAKIA[0-9A-Z]{16}\b/g, "[REDACTED_AWS_KEY]"],
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[REDACTED_JWT]"],
  [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, "[REDACTED_PRIVATE_KEY]"],
  [/https:\/\/(?:discord(?:app)?\.com\/api\/webhooks|hooks\.slack\.com\/services)\/\S+/gi, "[REDACTED_WEBHOOK]"],
];

const IDENTIFIER_PATTERNS = [
  ["EMAIL", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
  ["IPV6", /(?<![0-9A-F:])(?:(?:[0-9A-F]{1,4}:){1,6}:|::)(?:ffff(?::0{1,4})?:)?(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])/gi],
  ["IPV6", /(?<![0-9A-F:])(?:(?:[0-9A-F]{1,4}:){7}[0-9A-F]{1,4}|(?:[0-9A-F]{1,4}:){1,7}:|(?:[0-9A-F]{1,4}:){1,6}:[0-9A-F]{1,4}|(?:[0-9A-F]{1,4}:){1,5}(?::[0-9A-F]{1,4}){1,2}|(?:[0-9A-F]{1,4}:){1,4}(?::[0-9A-F]{1,4}){1,3}|(?:[0-9A-F]{1,4}:){1,3}(?::[0-9A-F]{1,4}){1,4}|(?:[0-9A-F]{1,4}:){1,2}(?::[0-9A-F]{1,4}){1,5}|[0-9A-F]{1,4}:(?:(?::[0-9A-F]{1,4}){1,6})|:(?:(?::[0-9A-F]{1,4}){1,7}|:))(?![0-9A-F:])/gi],
  ["MAC", /\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b/gi],
  ["VIN", /\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])(?=[A-HJ-NPR-Z0-9]*\d)[A-HJ-NPR-Z0-9]{17}\b/gi],
  ["HOME", /\/(?:Users|home)\/[^/\r\n]+/g],
  ["HOME", /[A-Z]:\\Users\\[^\\\r\n]+/gi],
];

const IPV4_PATTERN = /\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b/g;
const VERSION_CONTEXT_PATTERN = /(?:version|firmware|integration)\s*[:=]?\s*$/i;
const KEYED_IDENTIFIER_PATTERNS = [
  ["SERIAL", /((?<![A-Z0-9_-])["']?(?:serial(?:[_ -]?number)?|device[_ -]?id)["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
  ["USER", /((?<![A-Z0-9_-])["']?(?:user(?:name)?|login)["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
  ["DEVICE", /((?<![A-Z0-9_-])["']?(?:gateway[_ -]?id|asset[_ -]?site[_ -]?id|site[_ -]?id|din|warp[_ -]?site[_ -]?number|energy[_ -]?site)["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
];
const PREFIXED_IDENTIFIER_PATTERNS = [
  ["DEVICE", /(\bfor site\s+)([A-Za-z0-9-]{15,})/gi],
  ["DEVICE", /(\bsite\s+)(\d{13,})/gi],
  ["DEVICE", /(\benergy_sites?[/\s:=]+)(\d{13,})/gi],
];

async function pseudonymise(text, label, pattern, replacementsByLabel) {
  const values = [...new Set(text.match(pattern) ?? [])];
  const replacements = replacementsByLabel.get(label) ?? new Map();
  replacementsByLabel.set(label, replacements);
  for (const value of values) {
    const key = value.toLowerCase();
    if (!replacements.has(key)) {
      replacements.set(key, `[${label}_${replacements.size + 1}]`);
    }
  }
  return text.replace(
    pattern,
    (value) => replacements.get(value.toLowerCase()) ?? `[${label}]`,
  );
}

function nextReplacement(label, value, replacementsByLabel) {
  const replacements = replacementsByLabel.get(label) ?? new Map();
  replacementsByLabel.set(label, replacements);
  const key = value.toLowerCase();
  if (!replacements.has(key)) {
    replacements.set(key, `[${label}_${replacements.size + 1}]`);
  }
  return replacements.get(key);
}

function pseudonymiseIpv4(text, replacementsByLabel) {
  return text.replace(IPV4_PATTERN, (value, offset, source) => {
    const context = source.slice(Math.max(0, offset - 32), offset);
    return VERSION_CONTEXT_PATTERN.test(context)
      ? value
      : nextReplacement("IP", value, replacementsByLabel);
  });
}

function pseudonymiseKeyed(text, label, pattern, replacementsByLabel) {
  return text.replace(pattern, (_match, prefix, rawValue) => {
    const quote = rawValue[0];
    const isQuoted = (quote === '"' || quote === "'") && rawValue.endsWith(quote);
    const value = isQuoted ? rawValue.slice(1, -1) : rawValue;
    const replacement = nextReplacement(label, value, replacementsByLabel);
    return isQuoted ? `${prefix}${quote}${replacement}${quote}` : `${prefix}${replacement}`;
  });
}

export async function sanitizeSupportBundle(input) {
  let output = input.replaceAll("\0", "");
  const replacementsByLabel = new Map();
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    output = output.replace(pattern, replacement);
  }
  for (const [label, pattern] of IDENTIFIER_PATTERNS) {
    output = await pseudonymise(output, label, pattern, replacementsByLabel);
  }
  output = pseudonymiseIpv4(output, replacementsByLabel);
  for (const [label, pattern] of KEYED_IDENTIFIER_PATTERNS) {
    output = pseudonymiseKeyed(output, label, pattern, replacementsByLabel);
  }
  for (const [label, pattern] of PREFIXED_IDENTIFIER_PATTERNS) {
    output = pseudonymiseKeyed(output, label, pattern, replacementsByLabel);
  }
  const bundle = `${SANITISED_MARKER}\n${output}`;
  if (new TextEncoder().encode(bundle).byteLength > MAX_FILE_BYTES) {
    throw new RangeError("Sanitised bundle exceeds the 512 KB limit");
  }
  return bundle;
}

export function decodeUtf8SupportFile(bytes) {
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
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
      const decoded = decodeUtf8SupportFile(await file.arrayBuffer());
      const content = await sanitizeSupportBundle(decoded);
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
