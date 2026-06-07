const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/app.js", "utf8");
const match = source.match(/const BUILTIN_COMMANDS = \[[\s\S]*?\];/);

assert(match, "没有找到 BUILTIN_COMMANDS");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(`${match[0]}; this.BUILTIN_COMMANDS = BUILTIN_COMMANDS;`, sandbox);

const names = sandbox.BUILTIN_COMMANDS.map((command) => command.name);
assert(names.includes("/ps"), "命令面板应该保留 /ps 查看后台任务");
assert(names.includes("/kill"), "命令面板应该保留 /kill 终止后台任务");
assert(!names.includes("/jobs"), "命令面板不应该再显示 /jobs 重复查看命令");
assert(!names.includes("/stop"), "命令面板不应该再显示 /stop 重复终止命令");
