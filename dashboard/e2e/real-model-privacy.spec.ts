import { expect, test } from "@playwright/test";

import {
  assertPrivateTextAbsent,
  historyWithDecodedPayloads,
} from "./real-model-privacy";

test("privacy scan catches newline-bearing content in encoded history and JSON logs", () => {
  const privatePrefix = "Report heading\nTicket path";
  const encodedPayload = Buffer.from(
    JSON.stringify({ report_markdown: `${privatePrefix} and more` }),
    "utf8",
  ).toString("base64");
  const history = JSON.stringify({ payloads: [{ data: encodedPayload }] });
  const jsonLog = JSON.stringify({ content: `${privatePrefix} and more` });

  expect(() =>
    assertPrivateTextAbsent(
      "Temporal history",
      historyWithDecodedPayloads(history),
      [privatePrefix],
    ),
  ).toThrow("Private content was retained in Temporal history");
  expect(() =>
    assertPrivateTextAbsent("captured process logs", [jsonLog], [privatePrefix]),
  ).toThrow("Private content was retained in captured process logs");
});
