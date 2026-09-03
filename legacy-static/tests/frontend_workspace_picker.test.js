const assert = require("node:assert");

(async () => {
  const {
    chooseWorkspaceTabCompletion,
    groupWorkspaceDialogItems,
  } = await import("../static/js/components/workspace_picker.js");

  const completionChoice = chooseWorkspaceTabCompletion({
    currentValue: "/tmp/al",
    completion: { value: "/tmp/alpha-ap", is_final: false },
    candidates: ["/tmp/alpha-api", "/tmp/alpha-app"],
    selectedIndex: -1,
  });
  assert.equal(completionChoice.value, "/tmp/alpha-ap", "Tab 应优先补到后端返回的公共前缀");
  assert.equal(completionChoice.action, "complete");

  const selectedChoice = chooseWorkspaceTabCompletion({
    currentValue: "/tmp/alpha-ap",
    completion: { value: "/tmp/alpha-ap", is_final: false },
    candidates: ["/tmp/alpha-api", "/tmp/alpha-app"],
    selectedIndex: 1,
  });
  assert.equal(selectedChoice.value, "/tmp/alpha-app", "用户用方向键选中候选时 Tab 应采用选中项");
  assert.equal(selectedChoice.action, "select");

  const groups = groupWorkspaceDialogItems({
    query: "",
    recentProjects: ["/tmp/recent-nova"],
    candidates: ["/tmp/candidate-nova"],
  });
  assert.deepEqual(groups.map((group) => group.title), ["最近项目", "候选目录"], "空查询时弹窗应先展示最近项目");
})();
