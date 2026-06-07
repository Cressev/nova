const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/app.js", "utf8");
const match = source.match(/function projectName[\s\S]*?function renderSessionItem/);

assert(match, "没有找到会话分组相关函数");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(match[0].replace(/\nfunction renderSessionItem$/, ""), sandbox);

const groups = sandbox.groupSessionsByProject([
  {
    id: "chat_missing_workspace",
    title: "旧线程",
    workspace: null,
    updated_at: "2026-06-07T01:00:00Z",
  },
  {
    id: "chat_nova_a",
    title: "Nova A",
    workspace: "/mnt/d/documents/study/code/codex/nova",
    updated_at: "2026-06-07T02:00:00Z",
  },
  {
    id: "chat_nova_b",
    title: "Nova B",
    workspace: "/mnt/d/documents/work/nova",
    updated_at: "2026-06-07T03:00:00Z",
  },
]);

const groupNames = groups.map((group) => group.name);
assert(!groupNames.includes("unknown"), "历史分组不应该显示 unknown");
assert(groupNames.includes("未绑定项目"), "缺失 workspace 的旧线程应显示为未绑定项目");

const novaNames = groupNames.filter((name) => name.startsWith("nova"));
assert.strictEqual(novaNames.length, 2);
assert.notStrictEqual(novaNames[0], novaNames[1], "同名项目分组需要带父目录消歧");
