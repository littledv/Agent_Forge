function collectStringValues(value: unknown, output: string[]): void {
  if (typeof value === "string") {
    output.push(value);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectStringValues(item, output));
    return;
  }
  if (value === null || typeof value !== "object") {
    return;
  }
  Object.values(value).forEach((item) => collectStringValues(item, output));
}

export function historyWithDecodedPayloads(rawHistory: string): string[] {
  const searchable = [rawHistory];
  let history: unknown;
  try {
    history = JSON.parse(rawHistory) as unknown;
  } catch {
    throw new Error("Temporal history was not valid JSON");
  }
  collectStringValues(history, searchable);

  const visitPayloads = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visitPayloads);
      return;
    }
    if (value === null || typeof value !== "object") {
      return;
    }
    for (const [key, child] of Object.entries(value)) {
      if (
        key === "data" &&
        typeof child === "string" &&
        child.length > 0 &&
        child.length % 4 === 0 &&
        /^[A-Za-z0-9+/]*={0,2}$/.test(child)
      ) {
        try {
          const decoded = Buffer.from(child, "base64").toString("utf8");
          searchable.push(decoded);
          try {
            collectStringValues(JSON.parse(decoded) as unknown, searchable);
          } catch {
            // Non-JSON payloads remain covered by the decoded-text scan.
          }
        } catch {
          // Invalid payload data remains covered by the raw-history scan.
        }
      }
      visitPayloads(child);
    }
  };
  visitPayloads(history);
  return searchable;
}

function privateTextVariants(privateText: string): string[] {
  const normalized = privateText.replace(/\r\n/g, "\n");
  const escaped = JSON.stringify(normalized).slice(1, -1);
  return [...new Set([privateText, normalized, escaped])];
}

export function assertPrivateTextAbsent(
  source: string,
  haystacks: string[],
  privateTexts: string[],
): void {
  const retained = privateTexts.some((privateText) =>
    privateTextVariants(privateText).some((variant) =>
      haystacks.some((haystack) => haystack.includes(variant)),
    ),
  );
  if (retained) {
    throw new Error(`Private content was retained in ${source}`);
  }
}
