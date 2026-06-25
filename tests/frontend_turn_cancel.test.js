const assert = require("node:assert");
const fs = require("node:fs");

const app = fs.readFileSync("static/js/app.js", "utf8");
const css = fs.readFileSync("static/css/styles.css", "utf8");

assert(app.includes("cancelActiveTurn"), "前端应提供整轮对话停止函数");
assert(app.includes("AbortController"), "运行中的 stream 应可被 AbortController 中断");
assert(app.includes("/cancel"), "停止按钮必须调用真实 session cancel API");
assert(app.includes("state.streamAbortController"), "当前流的 abort controller 应保存在状态中");
assert(app.includes("stopButtonEl"), "运行中停止入口应该是独立按钮，不复用发送按钮");
assert(app.includes('sendButtonEl.dataset.mode = "queue"'), "运行中发送按钮应该切换为排队模式");
assert(!app.includes('sendButtonEl.dataset.mode = "stop"'), "发送按钮不能再被复用成停止按钮");
assert(app.includes('stopButtonEl.disabled = true'), "停止按钮空闲态必须置灰禁用");
assert(app.includes('stopButtonEl.disabled = false'), "发送后停止按钮必须立刻启用");
assert(!app.includes("stopButtonEl.hidden = true"), "停止按钮不能空闲隐藏，否则用户发送普通消息时看不到停止入口");
assert(!app.includes("payload?.cancel_requested"), "空闲点击 stop 时后端 cancel_requested 不能被误判为有运行中任务");
assert(app.includes("markRunningToolsAsCancelRequested"), "停止当前轮后应立即把本地运行中工具卡片标记为停止中");
assert(app.includes('querySelectorAll(".tool-event.running")'), "停止兜底必须覆盖所有运行中的工具卡片");
const abortIndex = app.indexOf("state.streamAbortController?.abort()");
const cancelApiIndex = app.indexOf("void api(`/api/chat/sessions/${encodeURIComponent(sessionId)}/cancel`");
assert(abortIndex !== -1 && cancelApiIndex !== -1 && abortIndex < cancelApiIndex, "点击停止必须先本地 abort stream，再异步通知后端 cancel");
assert(css.includes("#stop-button"), "独立停止按钮需要样式，用户能立即识别");
assert(css.includes("#stop-button:disabled"), "停止按钮空闲态需要明确置灰样式");
assert(css.includes("#send-button.queue"), "排队模式按钮需要独立样式");
