export const SANITISED_MARKER = "PowerSync sanitised support bundle v1";
export const MAX_FILE_BYTES = 512 * 1024;
export const ALLOWED_SOURCE_EXTENSIONS = Object.freeze([
  ".txt",
  ".log",
  ".json",
  ".jsonc",
  ".yaml",
  ".yml",
  ".md",
  ".debug",
]);

const YAML_SECRET_SCALAR_HEADER_PATTERN = /^([ \t]*(?:-[ \t]+)?)(["']?(?!timezone[_ -]?token["']?\s*:)(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|api[_ -]?key|app[_ -]?secret|client[_ -]?secret|private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie)["']?\s*:\s*)(.*)$/i;
const SECRET_KEY_NAME_PATTERN = /^(?!timezone[_ -]?token$)(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|api[_ -]?key|app[_ -]?secret|client[_ -]?secret|private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie|authorization|set[_ -]?cookie)$/i;
const URL_USERINFO_PATTERN = /\b([a-z][a-z0-9+.-]*:\/\/)([^/@\s]+)@/gi;
const HTML_ENTITY_PATTERN = /&(?:quot|apos|amp|lt|gt|colon|#\d+|#x[0-9a-f]+);/gi;
const NON_IDENTIFYING_PRIMITIVE_PATTERN = /^(?:null|true|false)$/i;
const COOKIE_ARRAY_HEADER_PATTERN = /["']?cookies["']?\s*:\s*\[/gi;
const COOKIE_VALUE_PATTERN = /((?<![A-Z0-9_-])["']?value["']?\s*:\s*)(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n,}\]]+)/gi;

const SECRET_PATTERNS = [
  [
    /((?<![A-Z0-9_-])["']?authorization["']?\s*:\s*)(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:bearer|basic)\s+[^"'\s,}]+|[^\r\n]+)/gi,
    '$1"[REDACTED]"',
  ],
  [/["']?(?:set-cookie|cookie)["']?\s*:\s*(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\r\n]+)/gi, "Cookie: [REDACTED]"],
  [
    /((?<![A-Z0-9_-])["']?(?!timezone[_ -]?token["']?\s*[:=])(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|api[_ -]?key|app[_ -]?secret|client[_ -]?secret|private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie)["']?\s*[:=]\s*)(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]*)/gi,
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
  [/(?:Exponent|Expo)PushToken\[[^\]\s]{10,}\]/gi, "[REDACTED_PUSH_TOKEN]"],
  [/\b[A-Za-z0-9_-]{20,}:[A-Za-z0-9_-]{20,}\b/g, "[REDACTED_PUSH_TOKEN]"],
  [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, "[REDACTED_PRIVATE_KEY]"],
  [/https:\/\/(?:discord(?:app)?\.com\/api\/webhooks|hooks\.slack\.com\/services)\/\S+/gi, "[REDACTED_WEBHOOK]"],
];

const IDENTIFIER_PATTERNS = [
  ["EMAIL", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
  ["IPV6", /(?<![0-9A-F:])(?:(?:[0-9A-F]{1,4}:){1,6}:|::)(?:ffff(?::0{1,4})?:)?(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])/gi],
  ["IPV6", /(?<![0-9A-F:])(?:(?:[0-9A-F]{1,4}:){7}[0-9A-F]{1,4}|(?:[0-9A-F]{1,4}:){1,7}:|(?:[0-9A-F]{1,4}:){1,6}:[0-9A-F]{1,4}|(?:[0-9A-F]{1,4}:){1,5}(?::[0-9A-F]{1,4}){1,2}|(?:[0-9A-F]{1,4}:){1,4}(?::[0-9A-F]{1,4}){1,3}|(?:[0-9A-F]{1,4}:){1,3}(?::[0-9A-F]{1,4}){1,4}|(?:[0-9A-F]{1,4}:){1,2}(?::[0-9A-F]{1,4}){1,5}|[0-9A-F]{1,4}:(?:(?::[0-9A-F]{1,4}){1,6})|:(?:(?::[0-9A-F]{1,4}){1,7}|:))(?![0-9A-F:])/gi],
  ["MAC", /\b(?:(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}|(?:[0-9A-F]{4}\.){2}[0-9A-F]{4})\b/gi],
  ["VIN", /\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])(?=[A-HJ-NPR-Z0-9]*\d)[A-HJ-NPR-Z0-9]{17}\b/gi],
  ["HOME", /\/(?:Users|home)\/[^/\r\n]+/g],
  ["HOME", /[A-Z]:\\Users\\[^\\\r\n]+/gi],
];

const IPV4_PATTERN = /\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b/g;
const VERSION_CONTEXT_PATTERN = /(?:version|firmware[_ -]?version|integration[_ -]?version)\s*[:=]?\s*$/i;
const KEYED_IDENTIFIER_PATTERNS = [
  ["SERIAL", /((?<![A-Z0-9_-])["']?[A-Z0-9_-]*serial(?:[_ -]?number)?["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
  ["USER", /((?<![A-Z0-9_-])["']?(?:[A-Z0-9_-]*user(?:name)?|[A-Z0-9_-]*login|account[_ -]?(?:number|name|address)|concession[_ -]?address|street[_ -]?address|document[_ -]?id|email[_ -]?address|invoice[_ -]?number|identifier)["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
  ["DEVICE", /((?<![A-Z0-9_-])["']?(?:[A-Z0-9_-]*(?:gateway[_ -]?id|device[_ -]?(?:id|sn|name)|site[_ -]?(?:id|identifier))|din|nmi|warp[_ -]?site[_ -]?number|energy[_ -]?site|site[_ -]?address)["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi],
];
const ADDRESS_IDENTIFIER_PATTERN = /((?<![A-Z0-9_-])["']?address["']?\s*[:=]\s*)("(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)/gi;
const PREFIXED_IDENTIFIER_PATTERNS = [
  ["DEVICE", /(\bfor site\s+)([A-Za-z0-9-]{15,})/gi],
  ["DEVICE", /(\bsite\s+)(\d{13,})/gi],
  ["DEVICE", /(\benergy_sites?[/\s:=]+)(\d{13,})/gi],
  ["DEVICE", /(\bdevice\s*=\s*)(.*?)(?=,\s*[A-Z0-9_]+\s*=|$)/gim],
];

export function isAllowedSourceFileName(fileName) {
  const lowerName = fileName.toLowerCase();
  return ALLOWED_SOURCE_EXTENSIONS.some((extension) => lowerName.endsWith(extension));
}

function decodeHtmlEntities(text) {
  let output = text;
  for (let pass = 0; pass < 32; pass += 1) {
    const decoded = output.replace(HTML_ENTITY_PATTERN, (entity) => {
      const normalized = entity.toLowerCase();
      const named = {
        "&quot;": '"',
        "&apos;": "'",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&colon;": ":",
      }[normalized];
      if (named !== undefined) return named;
      const hexadecimal = normalized.startsWith("&#x");
      const digits = hexadecimal ? normalized.slice(3, -1) : normalized.slice(2, -1);
      const codePoint = Number.parseInt(digits, hexadecimal ? 16 : 10);
      return Number.isSafeInteger(codePoint)
        && codePoint >= 0
        && codePoint <= 0x10ffff
        && !(codePoint >= 0xd800 && codePoint <= 0xdfff)
        ? String.fromCodePoint(codePoint)
        : entity;
    });
    if (decoded === output) return output;
    output = decoded;
  }
  throw new Error("HTML entity nesting exceeds the sanitizer limit");
}

function redactYamlSecretScalars(text) {
  const parts = text.split(/(\r?\n)/);
  for (let index = 0; index < parts.length; index += 2) {
    const line = parts[index];
    const header = line.match(YAML_SECRET_SCALAR_HEADER_PATTERN);
    if (header) {
      const delimiter = header[3].trimEnd().endsWith(",") ? "," : "";
      parts[index] = `${header[1]}${header[2]}"[REDACTED]"${delimiter}`;
      const baseIndent = header[1].length;
      for (let continuation = index + 2; continuation < parts.length; continuation += 2) {
        const continuationLine = parts[continuation];
        if (continuationLine.trim() === "") continue;
        const indentation = continuationLine.match(/^[ \t]*/)?.[0] ?? "";
        if (indentation.length <= baseIndent) break;
        parts[continuation] = `${indentation}[REDACTED]`;
      }
    }
  }
  return parts.join("");
}

function matchingJsonArrayEnd(text, start) {
  let depth = 0;
  let quote = null;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const character = text[index];
    if (quote !== null) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if (character === "[") depth += 1;
    else if (character === "]" && --depth === 0) return index;
  }
  return -1;
}

function redactCookieJarValues(text) {
  let output = "";
  let cursor = 0;
  COOKIE_ARRAY_HEADER_PATTERN.lastIndex = 0;
  for (
    let header = COOKIE_ARRAY_HEADER_PATTERN.exec(text);
    header !== null;
    header = COOKIE_ARRAY_HEADER_PATTERN.exec(text)
  ) {
    const arrayStart = COOKIE_ARRAY_HEADER_PATTERN.lastIndex - 1;
    const arrayEnd = matchingJsonArrayEnd(text, arrayStart);
    if (arrayEnd === -1) throw new Error("Malformed persisted cookie array");
    const cookieArray = text.slice(arrayStart, arrayEnd + 1).replace(
      COOKIE_VALUE_PATTERN,
      '$1"[REDACTED]"',
    );
    output += text.slice(cursor, arrayStart) + cookieArray;
    cursor = arrayEnd + 1;
    COOKIE_ARRAY_HEADER_PATTERN.lastIndex = cursor;
  }
  return output + text.slice(cursor);
}

function precedingBackslashes(text, quoteIndex) {
  let count = 0;
  for (let index = quoteIndex - 1; index >= 0 && text[index] === "\\"; index -= 1) {
    count += 1;
  }
  return count;
}

function nextQuoteAtDepth(text, start, depth) {
  for (let index = start; index < text.length; index += 1) {
    if (text[index] === '"' && precedingBackslashes(text, index) === depth) return index;
  }
  return -1;
}

function redactSerializedJsonSecrets(text) {
  let output = "";
  let cursor = 0;
  while (cursor < text.length) {
    const keyOpen = text.indexOf('"', cursor);
    if (keyOpen === -1) break;
    const depth = precedingBackslashes(text, keyOpen);
    if (depth === 0 || depth % 2 === 0) {
      output += text.slice(cursor, keyOpen + 1);
      cursor = keyOpen + 1;
      continue;
    }
    const keyClose = nextQuoteAtDepth(text, keyOpen + 1, depth);
    if (keyClose === -1) break;
    const key = text.slice(keyOpen + 1, keyClose - depth);
    let valueOpen = keyClose + 1;
    while (/\s/.test(text[valueOpen] ?? "")) valueOpen += 1;
    if (text[valueOpen] !== ":" && text[valueOpen] !== "=") {
      output += text.slice(cursor, keyClose + 1);
      cursor = keyClose + 1;
      continue;
    }
    valueOpen += 1;
    while (/\s/.test(text[valueOpen] ?? "")) valueOpen += 1;
    const valueQuote = text.indexOf('"', valueOpen);
    const valueDepth = valueQuote !== -1
      && /^\\+$/.test(text.slice(valueOpen, valueQuote))
      ? precedingBackslashes(text, valueQuote)
      : 0;
    if (!SECRET_KEY_NAME_PATTERN.test(key) || valueDepth !== depth) {
      output += text.slice(cursor, keyClose + 1);
      cursor = keyClose + 1;
      continue;
    }
    const valueClose = nextQuoteAtDepth(text, valueQuote + 1, depth);
    if (valueClose === -1) break;
    output += `${text.slice(cursor, valueQuote + 1)}[REDACTED]${"\\".repeat(depth)}"`;
    cursor = valueClose + 1;
  }
  return output + text.slice(cursor);
}

function identifierLabelForKey(key) {
  const normalized = key.replace(/[^A-Za-z0-9]/g, "").toLowerCase();
  if (/serial(?:number)?$/.test(normalized)) return "SERIAL";
  if (/(?:gatewayid|deviceid|devicesn|devicename|siteid|siteidentifier|din|nmi|warpsitenumber|energysite)$/.test(normalized)) return "DEVICE";
  if (/(?:username|login|accountnumber|accountname|accountaddress|siteaddress|concessionaddress|streetaddress|documentid|emailaddress|invoicenumber|identifier)$/.test(normalized)) return "USER";
  return null;
}

function pseudonymiseSerializedJsonIdentifiers(text, replacementsByLabel) {
  let output = "";
  let cursor = 0;
  while (cursor < text.length) {
    const keyOpen = text.indexOf('"', cursor);
    if (keyOpen === -1) break;
    const depth = precedingBackslashes(text, keyOpen);
    if (depth === 0 || depth % 2 === 0) {
      output += text.slice(cursor, keyOpen + 1);
      cursor = keyOpen + 1;
      continue;
    }
    const keyClose = nextQuoteAtDepth(text, keyOpen + 1, depth);
    if (keyClose === -1) break;
    const key = text.slice(keyOpen + 1, keyClose - depth);
    let valueOpen = keyClose + 1;
    while (/\s/.test(text[valueOpen] ?? "")) valueOpen += 1;
    if (text[valueOpen] !== ":" && text[valueOpen] !== "=") {
      output += text.slice(cursor, keyClose + 1);
      cursor = keyClose + 1;
      continue;
    }
    valueOpen += 1;
    while (/\s/.test(text[valueOpen] ?? "")) valueOpen += 1;
    const valueQuote = text.indexOf('"', valueOpen);
    const valueDepth = valueQuote !== -1
      && /^\\+$/.test(text.slice(valueOpen, valueQuote))
      ? precedingBackslashes(text, valueQuote)
      : 0;
    const label = identifierLabelForKey(key);
    if (label === null || valueDepth !== depth) {
      output += text.slice(cursor, keyClose + 1);
      cursor = keyClose + 1;
      continue;
    }
    const valueClose = nextQuoteAtDepth(text, valueQuote + 1, depth);
    if (valueClose === -1) break;
    const value = text.slice(valueQuote + 1, valueClose - depth);
    const replacement = /^\[(?:SERIAL|USER|DEVICE)_\d+\]$/i.test(value)
      ? value
      : nextReplacement(label, value, replacementsByLabel);
    output += `${text.slice(cursor, valueQuote + 1)}${replacement}${"\\".repeat(depth)}"`;
    cursor = valueClose + 1;
  }
  return output + text.slice(cursor);
}

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
    if (!isQuoted && NON_IDENTIFYING_PRIMITIVE_PATTERN.test(value.trim())) {
      return `${prefix}${rawValue}`;
    }
    const replacement = nextReplacement(label, value, replacementsByLabel);
    return isQuoted ? `${prefix}${quote}${replacement}${quote}` : `${prefix}${replacement}`;
  });
}

function pseudonymiseAddress(text, replacementsByLabel) {
  return text.replace(ADDRESS_IDENTIFIER_PATTERN, (_match, prefix, rawValue) => {
    const trimmedValue = rawValue.trim();
    if (/^\d+$/.test(trimmedValue)) return `${prefix}${rawValue}`;
    const quote = trimmedValue[0];
    const isQuoted = (quote === '"' || quote === "'") && trimmedValue.endsWith(quote);
    const value = isQuoted ? trimmedValue.slice(1, -1) : trimmedValue;
    const replacement = nextReplacement("USER", value, replacementsByLabel);
    return isQuoted ? `${prefix}${quote}${replacement}${quote}` : `${prefix}${replacement}`;
  });
}

export async function sanitizeSupportBundle(input) {
  let output = redactSerializedJsonSecrets(
    redactCookieJarValues(
      redactYamlSecretScalars(decodeHtmlEntities(input.replaceAll("\0", ""))),
    ),
  );
  const replacementsByLabel = new Map();
  output = output.replace(URL_USERINFO_PATTERN, "$1");
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    output = output.replace(pattern, replacement);
  }
  output = pseudonymiseSerializedJsonIdentifiers(output, replacementsByLabel);
  for (const [label, pattern] of IDENTIFIER_PATTERNS) {
    output = await pseudonymise(output, label, pattern, replacementsByLabel);
  }
  output = pseudonymiseIpv4(output, replacementsByLabel);
  for (const [label, pattern] of KEYED_IDENTIFIER_PATTERNS) {
    output = pseudonymiseKeyed(output, label, pattern, replacementsByLabel);
  }
  output = pseudonymiseAddress(output, replacementsByLabel);
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
    const unsupported = file && !isAllowedSourceFileName(file.name);
    createButton.disabled = !file || file.size > MAX_FILE_BYTES || unsupported;
    status.textContent = unsupported
      ? "Choose a supported text log file. CSV and TSV exports are not accepted."
      : file?.size > MAX_FILE_BYTES
      ? "That file exceeds the 512 KB limit. Export a smaller relevant log window."
      : file
        ? "Ready to create a local sanitised copy."
        : "Choose one text log up to 512 KB.";
  });

  createButton.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file || file.size > MAX_FILE_BYTES || !isAllowedSourceFileName(file.name)) return;
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
