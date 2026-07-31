import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const mcpServer = path.join(testDirectory, "..", "scripts", "mcp-server.mjs");

const normalRuntimeSource = [
  "import json, os, pathlib, sys",
  "root = pathlib.Path(__file__).parent",
  "command = sys.argv[-1]",
  "with (root / 'commands.log').open('a', encoding='utf-8') as handle:",
  "    handle.write(command + '\\n')",
  "if command == 'ensure':",
  "    value = {'ok': True, 'state': 'running', 'managed': True, 'started': False, 'pid': 123, 'max_context': 4096}",
  "elif command == 'status':",
  "    value = {'ok': True, 'state': 'running', 'managed': True, 'pid': 123, 'path': os.getenv('PATH'), 'unrelated_secret_visible': os.getenv('UNRELATED_TEST_SECRET') is not None}",
  "elif command == 'request':",
  "    body = json.load(sys.stdin)",
  "    (root / 'request.json').write_text(json.dumps(body), encoding='utf-8')",
  "    value = {",
  "        'ok': True,",
  "        'elapsed_ms': 250,",
  "        'payload': {",
  "            'id': 'chatcmpl-test',",
  "            'object': 'chat.completion',",
  "            'model': 'supergemma-4-26b-a4b-uncensored',",
  "            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'SECOND_OPINION'}, 'finish_reason': 'stop'}],",
  "            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12},",
  "        },",
  "    }",
  "else:",
  "    value = {'ok': True, 'state': 'stopped', 'stopped': True, 'pid': 123}",
  "print(json.dumps(value))",
  "",
].join("\n");

const slowRuntimeSource = [
  "import json, os, pathlib, sys, time",
  "root = pathlib.Path(__file__).parent",
  "command = sys.argv[-1]",
  "with (root / 'commands.log').open('a', encoding='utf-8') as handle:",
  "    handle.write(command + '\\n')",
  "if command == 'ensure':",
  "    value = {'ok': True, 'state': 'running', 'managed': True, 'started': False, 'pid': 456, 'max_context': 4096}",
  "elif command == 'request':",
  "    json.load(sys.stdin)",
  "    with (root / 'request-pids.log').open('a', encoding='utf-8') as handle:",
  "        handle.write(str(os.getpid()) + '\\n')",
  "    time.sleep(60)",
  "    value = {'ok': False, 'error': 'unexpected_wakeup'}",
  "else:",
  "    value = {'ok': True, 'state': 'running', 'managed': True, 'pid': 456}",
  "print(json.dumps(value))",
  "",
].join("\n");

const slowEnsureRuntimeSource = [
  "import json, pathlib, sys, time",
  "root = pathlib.Path(__file__).parent",
  "command = sys.argv[-1]",
  "with (root / 'commands.log').open('a', encoding='utf-8') as handle:",
  "    handle.write(command + '\\n')",
  "if command == 'ensure':",
  "    (root / 'ensure-started').write_text('started', encoding='utf-8')",
  "    time.sleep(0.25)",
  "    (root / 'ensure-finished').write_text('finished', encoding='utf-8')",
  "    value = {'ok': True, 'state': 'running', 'managed': True, 'started': True, 'pid': 789, 'max_context': 4096}",
  "elif command == 'status':",
  "    value = {'ok': True, 'state': 'starting', 'managed': True, 'pid': 789}",
  "else:",
  "    value = {'ok': False, 'error': 'request_must_not_run'}",
  "print(json.dumps(value))",
  "",
].join("\n");

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForFile(filePath, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const value = await fs.readFile(filePath, "utf8");
      if (value.length > 0) return value;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    await delay(20);
  }
  throw new Error(`timed out waiting for ${filePath}`);
}

async function createHarness(context, runtimeSource = normalRuntimeSource, extraEnv = {}) {
  const temporary = await fs.mkdtemp(
    path.join(os.tmpdir(), "supergemma-mcp-test-"),
  );
  const fakeRuntime = path.join(temporary, "runtime.py");
  await fs.writeFile(fakeRuntime, runtimeSource, { mode: 0o600 });

  const child = spawn(process.execPath, [mcpServer], {
    stdio: ["pipe", "pipe", "pipe"],
    env: {
      ...process.env,
      SUPERGEMMA_RUNTIME_SCRIPT: fakeRuntime,
      SUPERGEMMA_REQUEST_TIMEOUT_MS: "5000",
      SUPERGEMMA_RUNTIME_TIMEOUT_MS: "5000",
      ...extraEnv,
    },
  });
  const exitPromise = once(child, "exit");
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  const pending = new Map();
  const observed = [];
  const waiters = [];
  const lines = readline.createInterface({
    input: child.stdout,
    crlfDelay: Infinity,
  });
  lines.on("line", (line) => {
    const message = JSON.parse(line);
    observed.push(message);
    const pendingRequest = pending.get(message.id);
    if (pendingRequest) {
      pending.delete(message.id);
      clearTimeout(pendingRequest.timer);
      pendingRequest.resolve(message);
    }
    for (let index = waiters.length - 1; index >= 0; index -= 1) {
      if (waiters[index].predicate(message)) {
        const waiter = waiters.splice(index, 1)[0];
        clearTimeout(waiter.timer);
        waiter.resolve(message);
      }
    }
  });
  child.on("exit", () => {
    for (const pendingRequest of pending.values()) {
      clearTimeout(pendingRequest.timer);
      pendingRequest.reject(
        new Error(`MCP server exited before responding: ${stderr}`),
      );
    }
    pending.clear();
  });

  let nextID = 1;
  const sendRequest = (method, params = {}, requestedID) => {
    const id = requestedID ?? nextID++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`timed out waiting for ${method} (${id}): ${stderr}`));
      }, 7_000);
      pending.set(id, { resolve, reject, timer });
      child.stdin.write(
        `${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`,
      );
    });
  };
  const sendNotification = (method, params = {}) => {
    child.stdin.write(
      `${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`,
    );
  };
  const sendUntrackedRequest = (id, method, params = {}) => {
    child.stdin.write(
      `${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`,
    );
  };
  const initialize = async (protocolVersion = "2025-06-18") => {
    const response = await sendRequest("initialize", {
      protocolVersion,
      capabilities: {},
      clientInfo: { name: "test", version: "1" },
    });
    sendNotification("notifications/initialized");
    return response;
  };
  const waitForMessage = (predicate, timeoutMs = 5_000) => {
    const existing = observed.find(predicate);
    if (existing) return Promise.resolve(existing);
    return new Promise((resolve, reject) => {
      const waiter = { predicate, resolve, reject, timer: null };
      waiter.timer = setTimeout(() => {
        const index = waiters.indexOf(waiter);
        if (index !== -1) waiters.splice(index, 1);
        reject(new Error(`timed out waiting for MCP message: ${stderr}`));
      }, timeoutMs);
      waiters.push(waiter);
    });
  };

  let closePromise = null;
  const close = () => {
    if (closePromise) return closePromise;
    closePromise = (async () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.stdin.end();
        const exited = await Promise.race([
          exitPromise.then(() => true),
          delay(2_000).then(() => false),
        ]);
        if (!exited) {
          child.kill("SIGKILL");
          await exitPromise;
        }
      }
      lines.close();
    })();
    return closePromise;
  };

  const harness = {
    child,
    close,
    fakeRuntime,
    initialize,
    messages: () => observed,
    sendNotification,
    sendRequest,
    sendUntrackedRequest,
    stderr: () => stderr,
    temporary,
    waitForMessage,
    writeRaw: (value) => child.stdin.write(value),
  };
  context.after(async () => {
    await close();
    await fs.rm(temporary, { recursive: true, force: true });
  });
  return harness;
}

test("serves initialized MCP tools and forwards one bounded runtime request", async (context) => {
  const harness = await createHarness(context, normalRuntimeSource, {
    PATH: "/tmp/unsafe-parent-path",
    UNRELATED_TEST_SECRET: "must-not-reach-runtime",
  });
  const initialized = await harness.initialize("2025-06-18");
  assert.equal(initialized.result.protocolVersion, "2025-06-18");
  assert.equal(initialized.result.serverInfo.name, "supergemma-local");

  const listed = await harness.sendRequest("tools/list");
  assert.deepEqual(
    listed.result.tools.map((tool) => tool.name),
    ["ask_supergemma", "supergemma_status", "stop_supergemma"],
  );

  const status = await harness.sendRequest("tools/call", {
    name: "supergemma_status",
    arguments: {},
  });
  assert.equal(status.result.structuredContent.state, "running");
  assert.equal(
    status.result.structuredContent.unrelated_secret_visible,
    false,
  );
  assert.equal(
    status.result.structuredContent.path,
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
  );

  const asked = await harness.sendRequest("tools/call", {
    name: "ask_supergemma",
    arguments: {
      prompt: "Give a second opinion.",
      mode: "code_review",
      max_tokens: 32,
      temperature: 0,
    },
  });
  assert.equal(asked.result.isError, false);
  assert.equal(asked.result.structuredContent.response, "SECOND_OPINION");
  assert.equal(
    asked.result.structuredContent.trust,
    "untrusted_advisory_output",
  );
  assert.equal(
    asked.result.structuredContent.end_to_end_completion_tokens_per_second,
    8,
  );

  const receivedBody = JSON.parse(
    await fs.readFile(path.join(harness.temporary, "request.json"), "utf8"),
  );
  assert.equal(receivedBody.max_completion_tokens, 32);
  assert.equal(receivedBody.temperature, 0);
  assert.match(
    receivedBody.messages[0].content,
    /Review only the supplied code/,
  );
  const commands = (
    await fs.readFile(path.join(harness.temporary, "commands.log"), "utf8")
  )
    .trim()
    .split("\n");
  assert.deepEqual(commands, ["status", "ensure", "request"]);

  const stopped = await harness.sendRequest("tools/call", {
    name: "stop_supergemma",
    arguments: {},
  });
  assert.equal(stopped.result.structuredContent.stopped, true);
  await harness.close();
  assert.equal(harness.stderr(), "");
});

test("enforces initialize validation, lifecycle, supported versions, and request IDs", async (context) => {
  const harness = await createHarness(context);

  const premature = await harness.sendRequest("tools/list");
  assert.equal(premature.error.code, -32002);

  const malformed = await harness.sendRequest("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: [],
    clientInfo: { name: "test", version: "1" },
  });
  assert.equal(malformed.error.code, -32602);
  assert.match(malformed.error.data, /capabilities/);

  const initialized = await harness.sendRequest("initialize", {
    protocolVersion: "2099-01-01",
    capabilities: {},
    clientInfo: { name: "test", version: "1" },
  });
  assert.equal(initialized.result.protocolVersion, "2025-11-25");

  const missingNotification = await harness.sendRequest("tools/list");
  assert.equal(missingNotification.error.code, -32002);
  harness.sendNotification("notifications/initialized");
  const listed = await harness.sendRequest("tools/list");
  assert.equal(listed.result.tools.length, 3);

  const invalidIDResponse = harness.waitForMessage(
    (message) => message.id === null && message.error?.code === -32600,
  );
  harness.writeRaw(
    `${JSON.stringify({
      jsonrpc: "2.0",
      id: null,
      method: "ping",
      params: {},
    })}\n`,
  );
  assert.match((await invalidIDResponse).error.data, /string or number/);

  for (const version of ["2025-11-25", "2025-06-18"]) {
    const versionHarness = await createHarness(context);
    const response = await versionHarness.sendRequest("initialize", {
      protocolVersion: version,
      capabilities: {},
      clientInfo: { name: "test", version: "1" },
    });
    assert.equal(response.result.protocolVersion, version);
    await versionHarness.close();
  }

  const legacyHarness = await createHarness(context);
  const legacyResponse = await legacyHarness.sendRequest("initialize", {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "legacy-test", version: "1" },
  });
  assert.equal(legacyResponse.result.protocolVersion, "2025-11-25");
  await legacyHarness.close();
});

test("rejects unknown tools at the protocol layer and reports argument errors as tool results", async (context) => {
  const harness = await createHarness(context);
  await harness.initialize();

  const unknown = await harness.sendRequest("tools/call", {
    name: "not_a_tool",
    arguments: {},
  });
  assert.equal(unknown.error.code, -32602);
  assert.match(unknown.error.data, /unknown tool/);

  const invalid = await harness.sendRequest("tools/call", {
    name: "ask_supergemma",
    arguments: { prompt: "" },
  });
  assert.equal(invalid.result.isError, true);
  assert.equal(
    invalid.result.structuredContent.error,
    "invalid_tool_arguments",
  );
  assert.match(invalid.result.structuredContent.message, /non-empty/);

  const nonObject = await harness.sendRequest("tools/call", {
    name: "supergemma_status",
    arguments: [],
  });
  assert.equal(nonObject.error.code, -32602);
  assert.match(nonObject.error.data, /must be an object/);
});

test("rejects an over-budget context before invoking ensure", async (context) => {
  const harness = await createHarness(context);
  await harness.initialize();

  const response = await harness.sendRequest("tools/call", {
    name: "ask_supergemma",
    arguments: {
      prompt: "x".repeat(12000),
      max_tokens: 1024,
    },
  });
  assert.equal(response.result.isError, true);
  assert.equal(
    response.result.structuredContent.error,
    "invalid_tool_arguments",
  );
  assert.match(response.result.structuredContent.message, /estimated context/);
  await assert.rejects(
    fs.readFile(path.join(harness.temporary, "commands.log"), "utf8"),
    { code: "ENOENT" },
  );
});

test("limits concurrent calls and cancels active runtime children", async (context) => {
  const harness = await createHarness(context, slowRuntimeSource);
  await harness.initialize();

  const activeIDs = [40, 41, 42, 43];
  for (const id of activeIDs) {
    harness.sendUntrackedRequest(
      id,
      "tools/call",
      {
        name: "ask_supergemma",
        arguments: {
          prompt: `slow request ${id}`,
          max_tokens: 16,
        },
      },
    );
  }
  await waitForFile(path.join(harness.temporary, "request-pids.log"));

  const overflow = await harness.sendRequest(
    "tools/call",
    {
      name: "ask_supergemma",
      arguments: { prompt: "fifth request", max_tokens: 16 },
    },
    44,
  );
  assert.equal(overflow.error.code, -32000);

  for (const id of activeIDs) {
    harness.sendNotification("notifications/cancelled", {
      requestId: id,
      reason: "test cancellation",
    });
  }
  const status = await harness.sendRequest(
    "tools/call",
    { name: "supergemma_status", arguments: {} },
    45,
  );
  assert.equal(status.result.structuredContent.state, "running");
  await delay(150);
  assert.equal(
    harness.messages().some((message) => activeIDs.includes(message.id)),
    false,
  );
});

test("cancellation does not kill a lifecycle controller during startup", async (context) => {
  const harness = await createHarness(context, slowEnsureRuntimeSource);
  await harness.initialize();

  harness.sendUntrackedRequest(
    51,
    "tools/call",
    {
      name: "ask_supergemma",
      arguments: { prompt: "cancel during startup", max_tokens: 16 },
    },
  );
  await waitForFile(path.join(harness.temporary, "ensure-started"));
  harness.sendNotification("notifications/cancelled", {
    requestId: 51,
    reason: "test safe startup cancellation",
  });

  const status = await harness.sendRequest(
    "tools/call",
    { name: "supergemma_status", arguments: {} },
    52,
  );
  assert.equal(status.result.structuredContent.state, "starting");
  assert.equal(
    await waitForFile(path.join(harness.temporary, "ensure-finished")),
    "finished",
  );
  const commands = await fs.readFile(
    path.join(harness.temporary, "commands.log"),
    "utf8",
  );
  assert.deepEqual(commands.trim().split("\n"), ["ensure", "status"]);
  assert.equal(
    harness.messages().some((message) => message.id === 51),
    false,
  );
});

test("rejects an oversized JSONL line and recovers for the next request", async (context) => {
  const harness = await createHarness(context);
  const oversizedError = harness.waitForMessage(
    (message) => message.id === null && message.error?.code === -32700,
  );
  harness.writeRaw(`${"x".repeat(1024 * 1024 + 1)}\n`);
  assert.match((await oversizedError).error.data, /byte line limit/);

  const initialized = await harness.initialize();
  assert.equal(initialized.result.serverInfo.name, "supergemma-local");
});

test("rejects invalid UTF-8 JSONL input", async (context) => {
  const harness = await createHarness(context);
  const parseError = harness.waitForMessage(
    (message) => message.id === null && message.error?.code === -32700,
  );
  harness.writeRaw(Buffer.from([0xff, 0x0a]));
  assert.match((await parseError).error.data, /valid UTF-8/);
});

test("fails closed on an invalid timeout environment value", async () => {
  const child = spawn(process.execPath, [mcpServer], {
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      SUPERGEMMA_RUNTIME_TIMEOUT_MS: "not-a-number",
    },
  });
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const [exitCode] = await once(child, "exit");
  assert.notEqual(exitCode, 0);
  assert.match(stderr, /SUPERGEMMA_RUNTIME_TIMEOUT_MS must be a finite integer/);
});
