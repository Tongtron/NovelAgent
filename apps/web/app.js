const state = { health: null, projects: [], selectedId: null, detail: null, lastEvents: [], briefVersions: [], tagCatalog: null, characterArchive: [], selectedCharacterId: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const labels = {
  dashboard: "创作控制台",
  create: "创建小说",
  reader: "作品阅读",
  memory: "长期记忆",
  settings: "系统设置",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    const details = Array.isArray(payload.details) ? `：${payload.details.join("；")}` : "";
    throw new Error(`${payload.message || payload.error}${details}`);
  }
  return payload;
}

function notify(message, error = false) {
  const flash = $("#flash");
  flash.textContent = message;
  flash.className = `flash${error ? " error" : ""}`;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => flash.classList.add("hidden"), 5000);
}

function busy(show) { $("#busy").classList.toggle("hidden", !show); }

function switchView(view) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  $("#pageTitle").textContent = labels[view];
  if (view === "settings") loadSettings();
}

async function boot() {
  wireNavigation();
  await loadTags();
  await refresh();
}

function wireNavigation() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
  $$('[data-view-jump]').forEach((item) => item.addEventListener("click", () => switchView(item.dataset.viewJump)));
  $("#refreshButton").addEventListener("click", refresh);
  $("#rebuildCharactersButton").addEventListener("click", rebuildCharacterArchives);
  $("#characterBackButton").addEventListener("click", showCharacterList);
  $("#projectSelect").addEventListener("change", async (event) => {
    state.selectedId = event.target.value || null;
    localStorage.setItem("novelAgentProject", state.selectedId || "");
    await loadSelected();
  });
  $("#createForm").addEventListener("submit", createNovel);
  $$('#createForm input[name="audience_channel"]').forEach((input) => input.addEventListener("change", () => renderTagCatalog(input.value)));
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#confirmButton").addEventListener("click", () => projectAction("confirm"));
  $("#editBriefButton").addEventListener("click", () => $("#briefEditForm").classList.toggle("hidden"));
  $("#cancelBriefEdit").addEventListener("click", () => $("#briefEditForm").classList.add("hidden"));
  $("#briefEditForm").addEventListener("submit", saveBriefEdit);
  $("#regenerateBriefButton").addEventListener("click", () => regenerateBrief({ feedback: "", sections: [] }));
  $("#feedbackRegenerateButton").addEventListener("click", regenerateBriefFromFeedback);
  $("#deleteDraftButton").addEventListener("click", deleteProject);
  $("#briefVersionLeft").addEventListener("change", renderBriefComparison);
  $("#briefVersionRight").addEventListener("change", renderBriefComparison);
  $("#restoreBriefVersion").addEventListener("click", restoreBriefVersion);
  $("#recoverButton").addEventListener("click", () => projectAction("recover"));
  $("#pauseButton").addEventListener("click", () => {
    const action = state.detail?.project.status === "PAUSED" ? "resume" : "pause";
    projectAction(action);
  });
  $("#generateButton").addEventListener("click", generateChapter);
}

async function refresh() {
  try {
    const [health, projects] = await Promise.all([api("/api/health"), api("/api/novels")]);
    state.health = health;
    state.projects = projects.items;
    $("#runtimeMode").textContent = health.mode === "offline" ? "离线安全模式" : "在线模式";
    const enabledApis = Object.entries(health.apis).filter(([, item]) => item.enabled).map(([name]) => name.toUpperCase());
    $("#apiHint").textContent = enabledApis.length ? `${enabledApis.join(" · ")} 已启用` : "外部 API 未启用";
    const remembered = localStorage.getItem("novelAgentProject");
    if (!state.selectedId || !state.projects.some((p) => p.id === state.selectedId)) {
      state.selectedId = state.projects.some((p) => p.id === remembered) ? remembered : state.projects[0]?.id || null;
    }
    renderProjectSelect();
    await loadSelected();
  } catch (error) {
    $("#runtimeMode").textContent = "服务未连接";
    $("#apiHint").textContent = "请启动本地 API";
    notify(error.message, true);
  }
}

function renderProjectSelect() {
  const select = $("#projectSelect");
  select.innerHTML = state.projects.length
    ? state.projects.map((p) => `<option value="${p.id}" ${p.id === state.selectedId ? "selected" : ""}>${escapeHtml(p.title)}</option>`).join("")
    : '<option value="">尚无项目</option>';
}

async function loadSelected() {
  if (!state.selectedId) {
    state.detail = null;
    renderEmpty();
    return;
  }
  state.detail = await api(`/api/novels/${state.selectedId}`);
  renderDetail();
  const events = state.detail.latest_events || [];
  if (events.length) renderPipeline(events);
  if (state.detail.project.status === "DRAFT") await loadBriefVersions();
}

function renderEmpty() {
  $("#projectTitle").textContent = "让一个想法，变成持续生长的故事";
  $("#projectSynopsis").textContent = "选择标签并补充一句创意，系统会建立故事骨架，随后按章节规划、写作、审核和储备。";
  $("#projectStatus").textContent = "等待创建";
  $("#generateButton").disabled = true;
  $("#confirmButton").classList.add("hidden");
  $("#editBriefButton").classList.add("hidden");
  $("#regenerateBriefButton").classList.add("hidden");
  $("#deleteDraftButton").classList.add("hidden");
  $("#briefWorkbench").classList.add("hidden");
  $("#recoverButton").classList.add("hidden");
  $("#pauseButton").classList.add("hidden");
  ["todayCount", "reserveCount", "chapterCount"].forEach((id) => $(`#${id}`).textContent = "0");
  $("#healthScore").textContent = "--";
}

function renderDetail() {
  const { project, chapters, characters, world_facts: worldFacts, timeline, research_sources: researchSources, semantic_document_count: semanticCount, reserve_count: reserveCount } = state.detail;
  $("#projectTitle").textContent = project.title;
  $("#projectSynopsis").textContent = project.brief.synopsis;
  $("#projectStatus").textContent = statusLabel(project.status);
  $("#reserveCount").textContent = reserveCount;
  $("#reserveTarget").textContent = `目标 ${project.request.reserve_target}`;
  $("#chapterCount").textContent = chapters.filter((c) => ["READY", "SCHEDULED", "PUBLISHED"].includes(c.status)).length;
  $("#todayCount").textContent = chapters.filter((c) => c.created_at.slice(0, 10) === new Date().toISOString().slice(0, 10) && c.status === "READY").length;
  $("#healthScore").textContent = chapters.some((c) => c.status === "FAILED") ? "需处理" : "正常";
  const canGenerate = project.confirmed && project.status === "ACTIVE";
  $("#generateButton").disabled = !canGenerate;
  $("#confirmButton").classList.toggle("hidden", project.confirmed);
  const editableDraft = project.status === "DRAFT" && !project.confirmed && chapters.length === 0;
  $("#editBriefButton").classList.toggle("hidden", !editableDraft);
  $("#regenerateBriefButton").classList.toggle("hidden", !editableDraft);
  $("#briefWorkbench").classList.toggle("hidden", !editableDraft);
  $("#deleteDraftButton").classList.remove("hidden");
  const readyCount = chapters.filter((c) => ["READY", "SCHEDULED", "PUBLISHED"].includes(c.status)).length;
  if (project.status === "DRAFT" && !project.confirmed && chapters.length === 0) {
    $("#deleteDraftButton").textContent = "删除草稿";
  } else {
    $("#deleteDraftButton").textContent = "删除作品";
  }
  $("#recoverButton").classList.toggle("hidden", project.status !== "HUMAN_REQUIRED");
  $("#pauseButton").classList.toggle("hidden", !project.confirmed);
  $("#pauseButton").textContent = project.status === "PAUSED" ? "继续" : "暂停";
  renderBrief(project.brief, project.request);
  if (editableDraft) populateBriefEditor(project.brief);
  renderChapters(chapters);
  renderMemory(characters, worldFacts, timeline, researchSources || [], semanticCount || 0);
}

function renderBrief(brief, request) {
  $("#briefContent").innerHTML = `<div class="brief-grid">
    <div class="brief-block"><h4>频道与题材</h4><p><b>${escapeHtml(request.audience_channel || "男频")}</b><br>${escapeHtml(request.genre)} · ${request.experiences.map(escapeHtml).join(" / ") || "自动确定体验"}</p></div>
    <div class="brief-block"><h4>核心卖点</h4><ul>${brief.selling_points.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>
    <div class="brief-block"><h4>主角与主要冲突</h4><p><b>${escapeHtml(brief.protagonist)}</b><br>${escapeHtml(brief.main_conflict)}</p></div>
    <div class="brief-block"><h4>世界规则</h4><ul>${brief.world_rules.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>
    <div class="brief-block"><h4>连载计划</h4><p>${escapeHtml(brief.update_plan)}<br>预计 ${brief.total_chapters} 章 / ${brief.volume_count} 卷</p></div>
  </div>`;
}

function populateBriefEditor(brief) {
  const form = $("#briefEditForm");
  const set = (name, value) => { form.elements[name].value = value ?? ""; };
  set("selected_title", brief.selected_title);
  set("synopsis", brief.synopsis);
  set("target_readers", brief.target_readers);
  set("protagonist_name", brief.protagonist_name || brief.protagonist);
  set("protagonist_profile", brief.protagonist_profile);
  set("main_conflict", brief.main_conflict);
  set("reader_contract", brief.reader_contract);
  set("core_expectation", brief.core_expectation);
  set("selling_points", brief.selling_points.join("\n"));
  set("world_rules", brief.world_rules.join("\n"));
  set("opening_three_chapters", brief.opening_three_chapters.join("\n"));
  set("style_guide", brief.style_guide.join("\n"));
  set("total_chapters", brief.total_chapters);
  set("volume_count", brief.volume_count);
  set("update_plan", brief.update_plan);
}

function splitLines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function saveBriefEdit(event) {
  event.preventDefault();
  if (!state.selectedId || !state.detail) return;
  const form = new FormData(event.target);
  const current = state.detail.project.brief;
  const title = String(form.get("selected_title")).trim();
  const brief = {
    ...current,
    selected_title: title,
    title_candidates: [...new Set([title, ...current.title_candidates])].slice(0, 6),
    synopsis: String(form.get("synopsis")).trim(),
    target_readers: String(form.get("target_readers")).trim(),
    selling_points: splitLines(form.get("selling_points")),
    protagonist: String(form.get("protagonist_name")).trim(),
    protagonist_name: String(form.get("protagonist_name")).trim(),
    protagonist_profile: String(form.get("protagonist_profile")).trim(),
    main_conflict: String(form.get("main_conflict")).trim(),
    reader_contract: String(form.get("reader_contract")).trim(),
    core_expectation: String(form.get("core_expectation")).trim(),
    world_rules: splitLines(form.get("world_rules")),
    total_chapters: Number(form.get("total_chapters")),
    volume_count: Number(form.get("volume_count")),
    opening_three_chapters: splitLines(form.get("opening_three_chapters")),
    update_plan: String(form.get("update_plan")).trim(),
    style_guide: splitLines(form.get("style_guide")),
  };
  try {
    busy(true);
    await api(`/api/novels/${state.selectedId}/brief`, { method: "PUT", body: JSON.stringify(brief) });
    $("#briefEditForm").classList.add("hidden");
    notify("方案修改已保存为新版本。");
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function regenerateBriefFromFeedback() {
  const feedback = $("#briefFeedback").value.trim();
  const sections = [...$("#briefSections").querySelectorAll("input:checked")].map((item) => item.value);
  if (!feedback && !sections.length) {
    notify("请填写修改意见或选择需要重做的模块。", true);
    return;
  }
  await regenerateBrief({ feedback, sections });
}

async function regenerateBrief(payload) {
  if (!state.selectedId) return;
  try {
    busy(true);
    await api(`/api/novels/${state.selectedId}/brief-regenerate`, { method: "POST", body: JSON.stringify(payload) });
    $("#briefFeedback").value = "";
    $("#briefSections").querySelectorAll("input").forEach((item) => { item.checked = false; });
    notify(payload.sections.length ? "选中的方案模块已重做并保存为新版本。" : "已生成新的创作方案版本。");
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function loadBriefVersions() {
  const payload = await api(`/api/novels/${state.selectedId}/brief-versions`);
  state.briefVersions = payload.items;
  const options = state.briefVersions.map((item) => `<option value="${item.version}">v${item.version} · ${escapeHtml(versionSource(item.source))}</option>`).join("");
  $("#briefVersionLeft").innerHTML = options;
  $("#briefVersionRight").innerHTML = options;
  if (state.briefVersions.length > 1) $("#briefVersionLeft").value = state.briefVersions[1].version;
  if (state.briefVersions.length) $("#briefVersionRight").value = state.briefVersions[0].version;
  renderBriefComparison();
}

function renderBriefComparison() {
  const render = (target, version) => {
    const item = state.briefVersions.find((entry) => entry.version === Number(version));
    if (!item) { $(target).innerHTML = "暂无版本"; return; }
    const brief = item.brief;
    $(target).innerHTML = `<h4>v${item.version} · ${escapeHtml(versionSource(item.source))}</h4><p><strong>书名：</strong>${escapeHtml(brief.selected_title)}</p><p><strong>主角：</strong>${escapeHtml(brief.protagonist_name || brief.protagonist)}</p><p><strong>简介：</strong>${escapeHtml(brief.synopsis)}</p><p><strong>主要冲突：</strong>${escapeHtml(brief.main_conflict)}</p><p><strong>世界规则：</strong>${brief.world_rules.map(escapeHtml).join("；")}</p>${item.feedback ? `<p><strong>修改意见：</strong>${escapeHtml(item.feedback)}</p>` : ""}`;
  };
  render("#briefCompareLeft", $("#briefVersionLeft").value);
  render("#briefCompareRight", $("#briefVersionRight").value);
}

async function restoreBriefVersion() {
  if (!state.selectedId) return;
  const version = Number($("#briefVersionLeft").value);
  if (!version) return;
  try {
    busy(true);
    await api(`/api/novels/${state.selectedId}/brief-restore`, { method: "POST", body: JSON.stringify({ version }) });
    notify(`已恢复 v${version}，并保存为新的当前版本。`);
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function deleteProject() {
  if (!state.selectedId || !state.detail) return;
  const { project, chapters } = state.detail;
  const totalChapters = chapters.length;
  const readyCount = chapters.filter((c) => ["READY", "SCHEDULED", "PUBLISHED"].includes(c.status)).length;
  const editableDraft = project.status === "DRAFT" && !project.confirmed && totalChapters === 0;
  let message;
  if (editableDraft) {
    message = "确定删除这个未确认的草稿项目吗？此操作不能撤销。";
  } else if (totalChapters > 0) {
    message = `确定删除作品「${project.title}」吗？\n\n该项目共有 ${totalChapters} 章（其中 ${readyCount} 章已完成），所有人物档案、时间线、世界规则和章节数据都将被永久删除。\n\n此操作不可撤销！`;
  } else {
    message = `确定删除作品「${project.title}」吗？此操作将永久删除该项目及所有关联数据，不可撤销。`;
  }
  if (editableDraft) {
    if (!window.confirm(message)) return;
  } else {
    const entered = window.prompt(`${message}\n\n请输入完整书名“${project.title}”确认删除：`, "");
    if (entered === null) return;
    if (entered.trim() !== project.title) {
      notify("书名不匹配，已取消删除。", true);
      return;
    }
  }
  try {
    busy(true);
    await api(`/api/novels/${state.selectedId}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation_title: project.title }),
    });
    state.selectedId = null;
    localStorage.removeItem("novelAgentProject");
    notify("作品已删除。");
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

function versionSource(source) {
  return ({initial:"初始方案",migration:"历史方案",manual_edit:"手工修改",regenerate:"整体重做",partial_regenerate:"局部重做",feedback_regenerate:"按意见重做",restore:"恢复旧版"})[source] || source;
}

function renderChapters(chapters) {
  const rows = chapters.length ? chapters.slice().reverse().map((chapter) => chapterRow(chapter)).join("") : '<div class="empty">暂无章节</div>';
  $("#chapterQueue").innerHTML = rows;
  $("#readerChapterList").innerHTML = rows;
  $("#readerTotal").textContent = `${chapters.length} 章`;
  $$(".chapter-row").forEach((row) => row.addEventListener("click", () => {
    const chapter = chapters.find((item) => item.id === row.dataset.chapterId);
    void showChapterWithHistory(chapter);
  }));
  $$(".chapter-rework-button").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!state.selectedId) return;
      const chapterNumber = parseInt(btn.dataset.chapterNumber, 10);
      const feedback = window.prompt(
        `打回第 ${chapterNumber} 章重做\n\n请输入修改意见（可选）：\n例如：主角对话太生硬，需要更自然；战斗场景太短，需要扩展。\n\n此操作将重置第 ${chapterNumber} 章及之后所有章节，不可撤销。`,
        ""
      );
      if (feedback === null) return; // user cancelled
      try {
        busy(true);
        await api(`/api/novels/${state.selectedId}/chapters/${chapterNumber}/rework`, {
          method: "POST",
          body: JSON.stringify({ feedback: feedback.trim() }),
        });
        notify(
          feedback.trim()
            ? `第 ${chapterNumber} 章已打回重做，修改意见已记录。`
            : `第 ${chapterNumber} 章及后续章节已打回重做。`
        );
        await loadSelected();
      } catch (error) { notify(error.message, true); } finally { busy(false); }
    });
  });
}

function chapterRow(chapter) {
  const statusClass = chapter.status === "READY" ? "status-ready" : chapter.status === "FAILED" ? "status-failed" : "";
  const reworkable = chapter.status !== "PLANNED";
  const reworkBtn = reworkable
    ? `<button class="chapter-rework-button text-button" data-chapter-number="${chapter.number}" title="打回重做">↩ 重做</button>`
    : "";
  return `<div class="chapter-row" data-chapter-id="${chapter.id}"><div><b>${escapeHtml(chapter.title)}</b><span>版本 ${chapter.version} · ${escapeHtml(chapter.summary || "等待生成")}</span></div><div class="chapter-row-actions"><span class="${statusClass}">${chapter.status}</span>${reworkBtn}</div></div>`;
}

function chapterText(content, title) {
  const paragraphs = String(content || "").split(/\n\n+/).filter(Boolean);
  if (paragraphs[0] === title) paragraphs.shift();
  return paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("");
}

async function showChapterWithHistory(chapter) {
  switchView("reader");
  $$(".chapter-row").forEach((row) => row.classList.toggle("selected", row.dataset.chapterId === chapter.id));
  const isReady = chapter.status === "READY";
  const versionLabel = isReady ? "正式版本" : "草稿版本";
  let history = "";
  if (!isReady) {
    try {
      const payload = await api(`/api/novels/${state.selectedId}/chapters/${chapter.number}/versions`);
      const older = (payload.items || []).filter((version) => version.version !== chapter.version);
      if (older.length) {
        history = `<details class="chapter-history"><summary>历史草稿（${older.length} 个，当前显示 v${chapter.version}）</summary>${older.map((version) => `<details><summary>草稿 v${version.version} · ${escapeHtml(version.created_at)}</summary>${chapterText(version.content, chapter.title)}</details>`).join("")}</details>`;
      }
    } catch (error) {
      console.warn("chapter version history unavailable", error);
    }
  }
  const body = chapter.content
    ? chapterText(chapter.content, chapter.title)
    : '<div class="empty">这个章节还没有可阅读的正文。</div>';
  $("#readerContent").innerHTML = `<h2>${escapeHtml(chapter.title)}</h2><p class="reader-meta">${chapter.status} · ${versionLabel} v${chapter.version}</p>${history}${body}`;
}

function showChapterLegacy(chapter) {
  switchView("reader");
  $$(".chapter-row").forEach((row) => row.classList.toggle("selected", row.dataset.chapterId === chapter.id));
  if (!chapter.content) {
    $("#readerContent").innerHTML = '<div class="empty">这个章节还没有可阅读的正文。</div>';
    return;
  }
  const paragraphs = chapter.content.split(/\n\n+/).filter(Boolean);
  if (paragraphs[0] === chapter.title) paragraphs.shift();
  $("#readerContent").innerHTML = `<h2>${escapeHtml(chapter.title)}</h2><p class="reader-meta">${chapter.status} · 正式版本 ${chapter.version}</p>${paragraphs.map((p) => `<p>${escapeHtml(p)}</p>`).join("")}`;
}

const characterRoleLabels = {
  protagonist: "主角",
  supporting: "配角",
  ally: "盟友",
  rival: "竞争者",
  antagonist: "反派",
  mentor: "导师",
  family: "亲属",
  love_interest: "感情角色",
};
const characterStateLabels = {
  current_goal: "当前目标",
  last_result: "最近结果",
  goal: "长期目标",
  knowledge: "已知信息",
  trust: "信任度",
  location: "所在位置",
  condition: "身体状况",
  emotion: "情绪状态",
  inventory: "持有物资",
  relationship: "关系状态",
  relationships: "人物关系",
};
const characterStateOrder = ["current_goal", "last_result", "goal", "knowledge", "location", "condition", "emotion", "inventory", "relationship", "trust"];

function characterStateLabel(key) {
  return characterStateLabels[key] || key.replaceAll("_", " ");
}

function formatCharacterValue(value) {
  if (Array.isArray(value)) return value.length ? value.map(formatCharacterValue).join("；") : "暂无记录";
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    return entries.length ? entries.map(([key, item]) => `${characterStateLabel(key)}：${formatCharacterValue(item)}`).join("；") : "暂无记录";
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined || value === "") return "暂无记录";
  return String(value);
}

function characterFactParts(fact, characterName) {
  const text = String(fact || "").trim();
  if (!text || text === `主角姓名为${characterName}`) return null;
  if (text.startsWith("人物背景：")) return ["人物背景", text.slice(5)];
  if (text.startsWith("性格特征：")) return ["性格特征", text.slice(5)];
  if (text.startsWith("故事起点属于")) return ["故事起点", text.slice(6)];
  if (text.includes("无限资源") || text.includes("全知能力")) return ["能力边界", text];
  return ["固定设定", text];
}

function renderCharacterCard(item) {
  const currentState = item.current_state || {};
  const progressChapter = Number(currentState.last_seen_chapter ?? currentState.chapter ?? 0);
  const hasAppeared = progressChapter > 0;
  const progressText = hasAppeared ? `更新至第 ${progressChapter} 章` : "尚未正式出场";
  const roleText = characterRoleLabels[item.role] || "人物";
  const importanceText = currentState.importance === "core" || item.role === "protagonist" ? "核心人物" : "重要人物";
  const maxFacts = item.role === "protagonist" ? 6 : 3;
  const rawFacts = (item.immutable_facts || []).map((fact) => characterFactParts(fact, item.name)).filter(Boolean);
  const facts = [];
  for (const f of rawFacts) {
    if (facts.length >= maxFacts) break;
    if (f[0] === "固定设定" && facts.some((existing) => existing[1] === f[1])) continue;
    facts.push(f);
  }
  const stateRows = hasAppeared ? Object.entries(currentState)
    .filter(([key]) => !["chapter", "last_seen_chapter", "importance"].includes(key))
    .filter(([, value]) => {
      if (value === null || value === undefined || value === "") return false;
      if (Array.isArray(value) && value.length === 0) return false;
      if (typeof value === "object" && Object.keys(value).length === 0) return false;
      return true;
    })
    .sort(([left], [right]) => {
      const leftIndex = characterStateOrder.indexOf(left);
      const rightIndex = characterStateOrder.indexOf(right);
      return (leftIndex < 0 ? characterStateOrder.length : leftIndex) - (rightIndex < 0 ? characterStateOrder.length : rightIndex);
    }) : [];
  const factsHtml = facts.length
    ? facts.map(([label, value]) => `<div class="character-field"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")
    : "";
  const stateHtml = stateRows.length
    ? stateRows.map(([key, value]) => `<div class="character-field"><dt>${escapeHtml(characterStateLabel(key))}</dt><dd>${escapeHtml(formatCharacterValue(value))}</dd></div>`).join("")
    : "";

  return `<article class="character-card">
    <div class="character-head"><div><h4>${escapeHtml(item.name)}</h4><span>${escapeHtml(roleText)}</span><span class="importance-badge">${escapeHtml(importanceText)}</span></div><div class="character-progress">${escapeHtml(progressText)}<small>记忆版本 v${Number(item.version || 1)}</small></div></div>
    ${factsHtml ? `<details class="character-section" open><summary><h5>人物档案</h5></summary><dl>${factsHtml}</dl></details>` : ""}
    ${hasAppeared && stateHtml ? `<details class="character-section"><summary><h5>当前进展（${stateRows.length} 项）</h5></summary><dl>${stateHtml}</dl></details>` : ""}
    ${!hasAppeared ? `<p class="character-empty">尚未在正文中正式出场，档案数据来自创作方案。</p>` : ""}
  </article>`;
}

function characterListItem(item, selected) {
  const currentState = item.current_state || {};
  const progressChapter = Number(currentState.last_seen_chapter ?? currentState.chapter ?? 0);
  const progressText = progressChapter > 0 ? `最近第 ${progressChapter} 章` : "尚未正式出场";
  const roleText = characterRoleLabels[item.role] || "人物";
  const importanceText = currentState.importance === "core" || item.role === "protagonist" ? "核心" : "重要";
  const initial = Array.from(String(item.name || "人"))[0] || "人";
  return `<button class="character-list-item${selected ? " selected" : ""}" type="button" data-character-id="${escapeHtml(item.id)}" aria-pressed="${selected}">
    <span class="character-avatar">${escapeHtml(initial)}</span>
    <span class="character-list-copy"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(roleText)} · ${escapeHtml(importanceText)}人物</small><em>${escapeHtml(progressText)}</em></span>
    <span class="character-chevron">›</span>
  </button>`;
}

function selectCharacter(characterId, openDetail = false) {
  const selected = state.characterArchive.find((item) => item.id === characterId);
  if (!selected) return;
  state.selectedCharacterId = selected.id;
  $$("#characterList .character-list-item").forEach((button) => {
    const active = button.dataset.characterId === selected.id;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $("#characterDetail").innerHTML = renderCharacterCard(selected);
  if (openDetail) $("#characterBrowser").classList.add("show-detail");
}

function renderCharacterArchive(characters) {
  state.characterArchive = characters;
  $("#characterCount").textContent = `${characters.length} 人`;
  if (!characters.length) {
    state.selectedCharacterId = null;
    $("#characterList").innerHTML = '<div class="empty">暂无重要人物</div>';
    $("#characterDetail").innerHTML = '<div class="empty">暂无人物档案</div>';
    $("#characterBrowser").classList.remove("show-detail");
    return;
  }
  const selected = characters.find((item) => item.id === state.selectedCharacterId)
    || characters.find((item) => item.role === "protagonist")
    || characters[0];
  state.selectedCharacterId = selected.id;
  $("#characterList").innerHTML = characters.map((item) => characterListItem(item, item.id === selected.id)).join("");
  $$("#characterList .character-list-item").forEach((button) => button.addEventListener("click", () => selectCharacter(button.dataset.characterId, true)));
  $("#characterBrowser").classList.remove("show-detail");
  selectCharacter(selected.id);
}

function showCharacterList() {
  $("#characterBrowser").classList.remove("show-detail");
  const selected = $(`#characterList .character-list-item[data-character-id="${state.selectedCharacterId}"]`);
  selected?.focus();
}

function renderMemory(characters, facts, timeline, researchSources, semanticCount) {
  renderCharacterArchive(characters);
  const categoryGroups = {};
  facts.forEach((f) => { (categoryGroups[f.category] ||= []).push(f); });
  const factHtml = Object.entries(categoryGroups).map(([cat, items]) => {
    const body = items.map((f) => `<p>${escapeHtml(f.statement)} ${f.locked ? "🔒" : ""}</p>`).join("");
    return `<details class="fact-group"><summary>${escapeHtml(cat)} (${items.length})</summary><div class="fact-group-body">${body}</div></details>`;
  }).join("");
  $("#worldContent").innerHTML = facts.length ? factHtml : '<div class="empty">暂无规则</div>';
  const maxTimeline = 15;
  const visibleTimeline = timeline.slice(-maxTimeline);
  const hiddenCount = timeline.length - maxTimeline;
  const timelineHtml = visibleTimeline.map((item) => `<div class="timeline-item"><b>第 ${item.chapter_number} 章</b><p>${escapeHtml(item.event)}</p></div>`).join("");
  const expandHtml = hiddenCount > 0
    ? `<button class="text-button" id="expandTimeline" style="margin-top:8px;font-size:11px">展开全部 ${timeline.length} 条</button>
       <div id="hiddenTimeline" class="hidden">${timeline.slice(0, -maxTimeline).map((item) => `<div class="timeline-item"><b>第 ${item.chapter_number} 章</b><p>${escapeHtml(item.event)}</p></div>`).join("")}</div>`
    : "";
  $("#timelineContent").innerHTML = timeline.length
    ? timelineHtml + expandHtml
    : '<div class="empty">暂无正式事件</div>';
  if (hiddenCount > 0) {
    $("#expandTimeline").addEventListener("click", () => {
      $("#hiddenTimeline").classList.toggle("hidden");
      $("#expandTimeline").textContent = $("#hiddenTimeline").classList.contains("hidden")
        ? `展开全部 ${timeline.length} 条` : "收起";
    });
  }
  $("#semanticCount").textContent = `${semanticCount} 条语义索引`;
  const maxResearch = 5;
  const visibleResearch = researchSources.slice(0, maxResearch);
  const hiddenResearch = researchSources.length - maxResearch;
  const researchHtml = visibleResearch.map((item) => `<div class="research-card"><div><b>${escapeHtml(item.title || item.domain || "网页来源")}</b><span>${item.verified ? "已获得交叉来源" : "单一来源"} · 可信度 ${Number(item.reliability || 0).toFixed(2)}</span></div><p>${escapeHtml(item.excerpt || "")}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">打开来源 ↗</a>` : ""}</div>`).join("");
  const researchExpand = hiddenResearch > 0
    ? `<button class="text-button" id="expandResearch" style="margin-top:8px;font-size:11px">展开全部 ${researchSources.length} 条</button>
       <div id="hiddenResearch" class="hidden">${researchSources.slice(maxResearch).map((item) => `<div class="research-card"><div><b>${escapeHtml(item.title || item.domain || "网页来源")}</b><span>${item.verified ? "已获得交叉来源" : "单一来源"} · 可信度 ${Number(item.reliability || 0).toFixed(2)}</span></div><p>${escapeHtml(item.excerpt || "")}</p>${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">打开来源 ↗</a>` : ""}</div>`).join("")}</div>`
    : "";
  $("#researchContent").innerHTML = researchSources.length
    ? researchHtml + researchExpand
    : '<div class="empty">尚未触发现实知识调研</div>';
  if (hiddenResearch > 0) {
    $("#expandResearch").addEventListener("click", () => {
      $("#hiddenResearch").classList.toggle("hidden");
      $("#expandResearch").textContent = $("#hiddenResearch").classList.contains("hidden")
        ? `展开全部 ${researchSources.length} 条` : "收起";
    });
  }
}

async function rebuildCharacterArchives() {
  if (!state.selectedId) return notify("请先选择小说项目", true);
  if (!window.confirm("将扫描这本书的全部 READY 章节，只保留核心人物和重要人物；在线模式下会调用大模型。是否继续？")) return;
  busy(true);
  try {
    const result = await api(`/api/novels/${state.selectedId}/characters/rebuild`, {
      method: "POST",
      body: "{}",
    });
    await loadSelected();
    const failed = result.failures?.length ? `，${result.failures.length} 章提取失败` : "";
    const removed = result.removed_nonimportant ? `，清理 ${result.removed_nonimportant} 个非重要人物档案` : "";
    notify(`重要人物档案重建完成：扫描 ${result.processed_chapters} 章，共 ${result.character_count} 人${removed}${failed}`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    busy(false);
  }
}

function renderPipeline(events) {
  state.lastEvents = events;
  $("#pipeline").innerHTML = events.length ? events.map((event, index) => `<div class="pipeline-event"><span class="node-dot">${index + 1}</span><div><b>${escapeHtml(event.node)}</b><p>${escapeHtml(event.message)}</p></div><time>${event.progress}%</time></div>`).join("") : '<div class="pipeline-empty">生成章节后，这里会显示每个工作流节点。</div>';
}

async function loadTags() {
  state.tagCatalog = await api("/api/tags");
  const selected = $('#createForm input[name="audience_channel"]:checked')?.value || state.tagCatalog.default_channel;
  renderTagCatalog(selected);
}

function renderTagCatalog(channel) {
  if (!state.tagCatalog) return;
  const tags = state.tagCatalog.channels[channel];
  if (!tags) return;
  $("#genreSelect").innerHTML = tags.genres.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  ["experiences", "elements", "protagonist_tags"].forEach((group) => {
    const values = tags[group];
    const target = document.querySelector(`[data-check-group="${group}"]`);
    if (!target) return;
    target.innerHTML = values.map((value, index) => `<label class="chip"><input type="checkbox" name="${group}" value="${escapeHtml(value)}" ${index === 0 ? "checked" : ""}><span>${escapeHtml(value)}</span></label>`).join("");
  });
  $("#romanceSelect").innerHTML = tags.romance.map((value, index) => `<option value="${index === 0 ? "" : escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  $("#tagCatalogNote").textContent = `${channel}标签已切换。${state.tagCatalog.note}`;
}

function splitList(value) { return value.split(/[，,]/).map((x) => x.trim()).filter(Boolean); }

async function createNovel(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const checked = (name) => [...event.target.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
  const payload = {
    preferred_title: form.get("preferred_title") || null, audience_channel: form.get("audience_channel"), genre: form.get("genre"), experiences: checked("experiences"), elements: checked("elements"), protagonist_tags: checked("protagonist_tags"),
    romance: form.get("romance") || null, idea: form.get("idea"), must_have: splitList(form.get("must_have")), nice_to_have: splitList(form.get("nice_to_have")), exclude: splitList(form.get("exclude")),
    chapter_target_chars: Number(form.get("chapter_target_chars")), daily_chapters: Number(form.get("daily_chapters")), reserve_target: Number(form.get("reserve_target")), require_plan_confirmation: form.has("require_plan_confirmation"),
  };
  try {
    busy(true);
    const project = await api("/api/novels", { method: "POST", body: JSON.stringify(payload) });
    state.selectedId = project.id;
    localStorage.setItem("novelAgentProject", project.id);
    notify("创作方案已经生成，请检查后确认。")
    await refresh();
    switchView("dashboard");
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function projectAction(action) {
  if (!state.selectedId) return;
  try {
    busy(true);
    await api(`/api/novels/${state.selectedId}/${action}`, { method: "POST", body: "{}" });
    notify({confirm:"创作方案已确认，智能体可以开始连载。",pause:"项目已暂停。",resume:"项目已恢复。",recover:"已修复主角姓名数据，项目可以重新生成。"}[action]);
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function generateChapter() {
  if (!state.selectedId) return;
  try {
    busy(true);
    const payload = await api(`/api/novels/${state.selectedId}/runs`, { method: "POST", body: JSON.stringify({ count: 1 }) });
    const result = payload.items[0];
    renderPipeline(result.events);
    if (result.chapter.status === "READY") {
      notify(`${result.chapter.title} 已通过审核并进入 READY 储备。`);
    } else {
      notify(`${result.chapter.title} 暂未通过质量门槛，已保留草稿；可再次生成进行定向重写。`, true);
    }
    await refresh();
  } catch (error) { notify(error.message, true); } finally { busy(false); }
}

async function loadSettings() {
  try {
    const [settings, usage] = await Promise.all([
      api("/api/settings"),
      api("/api/usage").catch(() => null),
    ]);
    renderUsage(usage);
    const form = $("#settingsForm");
    for (const [key, value] of Object.entries(settings.preferences)) {
      const input = form.elements[key];
      if (!input) continue;
      if (input.type === "checkbox") input.checked = Boolean(value); else input.value = value;
    }
  } catch (error) { notify(error.message, true); }
}

const usagePeriodState = {
  mode: "7",
  year: new Date().getFullYear(),
  month: new Date().getMonth(),
  pickerYear: new Date().getFullYear(),
};
let usageRequestId = 0;

function formatLocalDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function selectedUsagePeriod() {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let start = new Date(today);
  let end = new Date(today);
  let label = "最近 7 天";

  if (usagePeriodState.mode === "30") {
    start.setDate(start.getDate() - 29);
    label = "最近 30 天";
  } else if (usagePeriodState.mode === "month") {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
    label = "本月";
  } else if (usagePeriodState.mode === "custom") {
    start = new Date(usagePeriodState.year, usagePeriodState.month, 1);
    end = new Date(usagePeriodState.year, usagePeriodState.month + 1, 0);
    if (usagePeriodState.year === today.getFullYear() && usagePeriodState.month === today.getMonth()) {
      end = today;
    }
    label = `${usagePeriodState.year}年${usagePeriodState.month + 1}月`;
  } else {
    start.setDate(start.getDate() - 6);
  }

  return { startDate: formatLocalDate(start), endDate: formatLocalDate(end), label };
}

function syncUsagePeriodControls() {
  const { label } = selectedUsagePeriod();
  $("#usagePeriodLabel").textContent = label;
  $$('[data-usage-range]').forEach((button) => {
    const active = button.dataset.usageRange === usagePeriodState.mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderUsageMonthGrid();
}

function renderUsageMonthGrid() {
  const grid = $("#usageMonthGrid");
  if (!grid) return;
  const now = new Date();
  const months = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"];
  grid.innerHTML = months.map((name, month) => {
    const future = usagePeriodState.pickerYear > now.getFullYear()
      || (usagePeriodState.pickerYear === now.getFullYear() && month > now.getMonth());
    const active = usagePeriodState.mode === "custom"
      && usagePeriodState.year === usagePeriodState.pickerYear
      && usagePeriodState.month === month;
    return `<button type="button" data-usage-month="${month}"${future ? " disabled" : ""} class="${active ? "active" : ""}" aria-pressed="${active}">${name}</button>`;
  }).join("");
  $$('[data-usage-month]').forEach((button) => button.addEventListener("click", () => {
    usagePeriodState.mode = "custom";
    usagePeriodState.year = usagePeriodState.pickerYear;
    usagePeriodState.month = Number(button.dataset.usageMonth);
    syncUsagePeriodControls();
    closeUsagePeriodPicker();
    void renderUsage();
  }));
}

function closeUsagePeriodPicker() {
  $("#usagePeriodPopover")?.classList.add("hidden");
  $("#usagePeriodButton")?.setAttribute("aria-expanded", "false");
}

function initializeUsagePeriodPicker() {
  const yearSelect = $("#usageYearSelect");
  if (!yearSelect) return;
  const currentYear = new Date().getFullYear();
  yearSelect.innerHTML = Array.from({ length: currentYear - 1999 }, (_, index) => currentYear - index)
    .map((year) => `<option value="${year}">${year} 年</option>`)
    .join("");
  yearSelect.value = String(usagePeriodState.pickerYear);
  yearSelect.addEventListener("change", () => {
    usagePeriodState.pickerYear = Number(yearSelect.value);
    renderUsageMonthGrid();
  });

  $("#usagePeriodButton").addEventListener("click", () => {
    const popover = $("#usagePeriodPopover");
    const willOpen = popover.classList.contains("hidden");
    popover.classList.toggle("hidden", !willOpen);
    $("#usagePeriodButton").setAttribute("aria-expanded", String(willOpen));
    if (willOpen) syncUsagePeriodControls();
  });
  $$('[data-usage-range]').forEach((button) => button.addEventListener("click", () => {
    usagePeriodState.mode = button.dataset.usageRange;
    syncUsagePeriodControls();
    closeUsagePeriodPicker();
    void renderUsage();
  }));
  document.addEventListener("click", (event) => {
    if (!$("#usagePeriod")?.contains(event.target)) closeUsagePeriodPicker();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeUsagePeriodPicker();
  });
  syncUsagePeriodControls();
}

function formatTokens(value) {
  const tokens = Number(value || 0);
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}m`;
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(tokens);
}

async function renderUsage() {
  const requestId = ++usageRequestId;
  const { startDate, endDate, label } = selectedUsagePeriod();
  const panel = $("#usagePanel");
  const chart = $("#usageChart");
  syncUsagePeriodControls();
  panel?.classList.add("is-loading");
  panel?.setAttribute("aria-busy", "true");
  $("#usageModel").textContent = `${label} · 正在读取…`;

  let stats;
  try {
    const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
    stats = await api(`/api/token/statistics?${query}`);
  } catch {
    if (requestId !== usageRequestId) return;
    $("#usageModel").textContent = `${label} · 数据读取失败`;
    chart.innerHTML = '<div class="usage-empty"><strong>暂时无法读取 Token 数据</strong><span>请确认服务正常运行后重试。</span></div>';
    $("#usageSummary").innerHTML = "";
    return;
  } finally {
    if (requestId === usageRequestId) {
      panel?.classList.remove("is-loading");
      panel?.setAttribute("aria-busy", "false");
    }
  }

  if (requestId !== usageRequestId) return;
  if (!stats.dates || !stats.dates.length) {
    $("#usageModel").textContent = `${label} · ${startDate} 至 ${endDate}`;
    chart.innerHTML = '<div class="usage-empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z"/></svg><strong>所选周期暂无记录</strong><span>产生新的模型调用后，用量会自动显示在这里。</span></div>';
    $("#usageSummary").innerHTML = "";
    return;
  }

  $("#usageModel").textContent = `${label} · ${startDate} 至 ${endDate}`;
  $("#usageSummary").innerHTML = `
    <div class="usage-card"><span>今日消耗</span><strong>${formatTokens(stats.today_tokens)}</strong></div>
    <div class="usage-card"><span>周期总量</span><strong>${formatTokens(stats.period_tokens)}</strong></div>
    <div class="usage-card"><span>日均消耗</span><strong>${formatTokens(stats.avg_daily)}</strong></div>
    <div class="usage-card"><span>消耗峰值日</span><strong>${escapeHtml((stats.max_date || "").slice(5) || "--")}</strong></div>`;

  const maxTokens = Math.max(...stats.total_tokens.map(Number), 1);
  chart.innerHTML = `<div class="usage-bars" style="--usage-columns:${stats.dates.length}">${stats.dates.map((date, index) => {
    const total = Number(stats.total_tokens[index] || 0);
    const input = Number(stats.input_tokens[index] || 0);
    const output = Number(stats.output_tokens[index] || 0);
    const height = Math.max(3, Math.round((total / maxTokens) * 100));
    const inputShare = total ? Math.max(0, Math.min(100, (input / total) * 100)) : 0;
    const outputShare = total ? Math.max(0, Math.min(100 - inputShare, (output / total) * 100)) : 0;
    const title = `${date}｜总计 ${formatTokens(total)}｜输入 ${formatTokens(input)}｜输出 ${formatTokens(output)}`;
    const showValue = stats.dates.length <= 14 || index % 3 === 0 || index === stats.dates.length - 1;
    return `<div class="usage-bar-column" title="${escapeHtml(title)}"><div class="usage-bar-value">${showValue ? formatTokens(total) : ""}</div><div class="usage-bar-stack" style="height:${height}%"><span class="usage-bar-output" style="height:${outputShare}%"></span><span class="usage-bar-input" style="height:${inputShare}%"></span></div><span>${escapeHtml(date.slice(5))}</span></div>`;
  }).join("")}</div>`;
}

async function saveSettings(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = { daily_chapters: Number(form.get("daily_chapters")), reserve_target: Number(form.get("reserve_target")), budget_limit: Number(form.get("budget_limit")), debug_mode: form.has("debug_mode") };
  try { await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }); notify("本地偏好已保存。") } catch (error) { notify(error.message, true); }
}

function statusLabel(status) { return ({DRAFT:"等待方案确认",ACTIVE:"自动连载可用",PAUSED:"已暂停",HUMAN_REQUIRED:"需要人工处理",ARCHIVED:"已归档"})[status] || status; }

initializeUsagePeriodPicker();
boot();
