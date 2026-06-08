export function chooseWorkspaceTabCompletion({ currentValue, completion, candidates, selectedIndex }) {
  const selected = Array.isArray(candidates) && selectedIndex >= 0 ? candidates[selectedIndex] : "";
  if (selected) {
    return { value: selected, action: "select" };
  }

  const completionValue = completion?.value || "";
  if (completionValue && completionValue !== currentValue) {
    return { value: completionValue, action: completion?.is_final ? "final" : "complete" };
  }

  const first = Array.isArray(candidates) ? candidates[0] : "";
  if (first) {
    return { value: first, action: "select" };
  }

  return { value: currentValue || "", action: "noop" };
}

export function groupWorkspaceDialogItems({ query, recentProjects, candidates }) {
  const groups = [];
  const recent = Array.isArray(recentProjects) ? recentProjects : [];
  const candidateList = Array.isArray(candidates) ? candidates : [];
  const seen = new Set();

  if (!String(query || "").trim() && recent.length > 0) {
    groups.push({
      title: "最近项目",
      items: recent.filter((path) => {
        if (seen.has(path)) {
          return false;
        }
        seen.add(path);
        return true;
      }),
    });
  }

  const visibleCandidates = candidateList.filter((path) => {
    if (seen.has(path)) {
      return false;
    }
    seen.add(path);
    return true;
  });
  if (visibleCandidates.length > 0) {
    groups.push({ title: "候选目录", items: visibleCandidates });
  }

  return groups;
}
