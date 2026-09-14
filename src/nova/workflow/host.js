'use strict';
// workflow 脚本宿主（dsh workflow-worker-thread 对齐）。
// 模型写的 JS 协调脚本跑在 node:vm 受限上下文：只有 agent/pipeline/parallel/
// phase/log/args，没有 fs/net/timer/require。实际工作全部由 agent() 经 stdio
// JSON 协议交回 Python 侧的子代理执行——脚本只做协调（dsh 同语义）。
const fs = require('fs');
const vm = require('vm');

const scriptPath = process.argv[2];
let args = {};
try { args = JSON.parse(fs.readFileSync(process.argv[3], 'utf8') || '{}'); } catch { args = {}; }
const script = fs.readFileSync(scriptPath, 'utf8');

function send(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }

let seq = 0;
const pending = new Map();

function request(op, payload) {
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    send({ op, id, ...payload });
  });
}

// ---- 面向脚本的钩子 ----
const agent = (prompt, opts) => {
  if (typeof prompt !== 'string' || !prompt.trim()) {
    throw new Error('agent(prompt, opts?) requires a non-empty prompt string');
  }
  const o = opts && typeof opts === 'object' ? opts : {};
  return request('agent', {
    prompt,
    opts: {
      label: o.label === undefined ? undefined : String(o.label),
      phase: o.phase === undefined ? undefined : String(o.phase),
      schema: o.schema === undefined ? undefined : o.schema,
      provider: o.provider === undefined ? undefined : String(o.provider),
      model: o.model === undefined ? undefined : String(o.model),
    },
  });
};
const log = (message) => send({ op: 'log', message: String(message) });
const phase = (title) => send({ op: 'phase', title: String(title) });

// pipeline(items, ...stages)：逐 item 独立穿过多级 stage，级间无 barrier；
// 普通 stage 抛错 → 该 item 置 null 并跳过它剩余的 stage（dsh 语义）。
async function pipeline(items, ...stages) {
  if (!Array.isArray(items)) throw new Error('pipeline(items, ...stages) requires an array');
  for (const stage of stages) {
    if (typeof stage !== 'function') throw new Error('pipeline stages must be functions');
  }
  return Promise.all(items.map(async (item, index) => {
    let prev = item;
    for (const stage of stages) {
      try { prev = await stage(prev, item, index); }
      catch { return null; }
    }
    return prev;
  }));
}

// parallel(thunks)：全部并发；抛错的 thunk 置 null（dsh allSettled-null 语义）。
async function parallel(thunks) {
  if (!Array.isArray(thunks)) throw new Error('parallel(thunks) requires an array of functions');
  for (const t of thunks) {
    if (typeof t !== 'function') throw new Error('parallel thunks must be functions');
  }
  return Promise.all(thunks.map((t) => Promise.resolve().then(() => t()).catch(() => null)));
}

// ---- 回放 stdin 响应 ----
let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    if (!line.trim()) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    const resolve = pending.get(msg.id);
    if (resolve) {
      pending.delete(msg.id);
      resolve(msg.ok ? msg.result : null);
    }
  }
});
process.stdin.on('end', () => {
  for (const resolve of pending.values()) resolve(null);
  pending.clear();
});

// ---- 执行脚本（top-level await 允许：包一层 async IIFE；return 收值）----
const sandbox = { agent, pipeline, parallel, phase, log, args };
vm.createContext(sandbox);
try {
  const value = vm.runInContext('(async () => {\n' + script + '\n})()', sandbox, { displayErrors: true });
  Promise.resolve(value).then(
    (v) => send({ op: 'done', value: v }),
    (err) => send({ op: 'error', message: String((err && err.stack) || err) }),
  );
} catch (err) {
  send({ op: 'error', message: String((err && err.stack) || err) });
}
