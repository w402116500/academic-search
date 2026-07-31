const toast = document.querySelector(".toast");

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function navigateWithFeedback(button, target, message) {
  button.classList.add("busy");
  button.setAttribute("aria-busy", "true");
  showToast(message);
  window.setTimeout(() => {
    window.location.href = target;
  }, 420);
}

const AUTH_STORAGE_KEY = "prototype-auth-user";

const WORKSPACE_MENU_ENTRIES = [
  {
    id: "sleep",
    name: "睡眠与大学生心理健康",
    status: "文献筛选 · 26 篇待审核记录",
    href: "03-literature-results.html?workspace=sleep",
    recent: true,
  },
  {
    id: "green",
    name: "绿地可达性与老年人心理健康",
    status: "证据研究 · 8 篇全文可问答",
    href: "04-research-workspace.html",
    recent: true,
  },
  {
    id: "community",
    name: "社区协同设计与公共项目信任",
    status: "任务解析 · 等待确认研究计划",
    href: "02-intent-confirm.html",
    recent: false,
  },
];

function getCurrentPrototypeWorkspaceId() {
  const workspace = new URLSearchParams(window.location.search).get("workspace");
  if (workspace === "sleep" || workspace === "community") return workspace;

  const page = window.location.pathname.split("/").pop();
  if (page === "03-literature-results.html") return "sleep";
  if (page === "04-research-workspace.html" || page === "09-research-chat.html") return "green";
  if (page === "02-intent-confirm.html") return "community";
  if (page === "08-paper-detail.html") return "sleep";
  return null;
}

function renderWorkspaceMenuItem(workspace, currentWorkspaceId) {
  if (workspace.id === currentWorkspaceId) {
    return `<span class="workspace-menu-item is-current" aria-current="page"><i data-lucide="check"></i><span><strong>${escapeHTML(workspace.name)}</strong><small>${escapeHTML(workspace.status)}</small></span></span>`;
  }
  return `<a class="workspace-menu-item" href="${workspace.href}" role="menuitem"><i data-lucide="folder-kanban"></i><span><strong>${escapeHTML(workspace.name)}</strong><small>${escapeHTML(workspace.status)}</small></span></a>`;
}

function getWorkspaceMenuEntries(query = "") {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const entries = [...WORKSPACE_MENU_ENTRIES].sort((left, right) => Number(right.recent) - Number(left.recent));
  if (!normalizedQuery) return entries;
  return entries.filter((workspace) => `${workspace.name} ${workspace.status}`.toLocaleLowerCase().includes(normalizedQuery));
}

function renderWorkspaceMenuList(currentWorkspaceId, query = "") {
  const entries = getWorkspaceMenuEntries(query);
  if (!entries.length) {
    return `<div class="workspace-menu-empty" data-workspace-menu-empty><i data-lucide="search-x"></i><span>没有找到匹配的工作区</span><small>试试搜索名称或当前阶段</small></div>`;
  }

  const recentEntries = entries.filter((workspace) => workspace.recent);
  const otherEntries = entries.filter((workspace) => !workspace.recent);
  const groups = [];
  if (recentEntries.length) {
    groups.push(`<div class="workspace-menu-group"><div class="workspace-menu-section-label">最近使用</div>${recentEntries.map((workspace) => renderWorkspaceMenuItem(workspace, currentWorkspaceId)).join("")}</div>`);
  }
  if (otherEntries.length) {
    const otherLabel = query.trim() ? "全部工作区" : "其他工作区";
    groups.push(`<div class="workspace-menu-group"><div class="workspace-menu-section-label">${otherLabel}</div>${otherEntries.map((workspace) => renderWorkspaceMenuItem(workspace, currentWorkspaceId)).join("")}</div>`);
  }
  return groups.join("");
}

function renderWorkspaceMenu(currentWorkspaceId) {
  const list = renderWorkspaceMenuList(currentWorkspaceId);

  return `<div class="workspace-menu" data-workspace-menu role="menu" aria-label="切换研究工作区" hidden><div class="workspace-menu-heading"><label class="workspace-menu-search"><i data-lucide="search"></i><input data-workspace-search type="search" placeholder="搜索工作区" aria-label="搜索工作区" autocomplete="off" /></label><a href="01-research-entry.html" role="menuitem" aria-label="新建研究"><i data-lucide="plus"></i><span>新建研究</span></a></div><div class="workspace-menu-list" data-workspace-menu-list>${list}</div></div>`;
}

function renderStaticWorkspaceMenus() {
  const currentWorkspaceId = getCurrentPrototypeWorkspaceId();
  document.querySelectorAll("[data-workspace-menu-slot]").forEach((slot) => {
    slot.innerHTML = renderWorkspaceMenu(currentWorkspaceId);
  });
  refreshIcons();
}

function escapeHTML(value) {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function getPrototypeUser() {
  try {
    const user = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? "null");
    return user?.name && user?.email ? user : null;
  } catch {
    return null;
  }
}

function refreshIcons() {
  window.lucide?.createIcons();
}

function renderAuthAreas() {
  const user = getPrototypeUser();

  document.querySelectorAll("[data-auth-area]").forEach((area) => {
    if (!user) {
      area.innerHTML = '<a class="header-login" href="06-login.html">登录</a>';
      return;
    }

    const displayName = escapeHTML(user.name);
    const email = escapeHTML(user.email);
    const initial = escapeHTML(user.name.slice(0, 1));
    area.innerHTML = `<div class="workspace-menu-wrap"><button class="header-workspaces" data-workspace-switcher type="button" aria-haspopup="menu" aria-expanded="false"><i data-lucide="layout-grid"></i><span>工作区</span><i data-lucide="chevron-down"></i></button>${renderWorkspaceMenu(getCurrentPrototypeWorkspaceId())}</div><div class="account-menu-wrap"><button class="account-trigger" data-account-trigger type="button" aria-label="打开账号菜单" aria-expanded="false"><span class="account-avatar">${initial}</span><span>${displayName}</span><i data-lucide="chevron-down"></i></button><div class="account-menu" data-account-menu hidden><div class="account-menu-identity"><strong>${displayName}</strong><span>${email}</span></div><a class="account-menu-action" href="01-research-entry.html"><i data-lucide="plus"></i>开始新研究</a><button class="account-menu-action" data-account-settings type="button"><i data-lucide="settings"></i>账号设置</button><button class="account-menu-action" data-logout type="button"><i data-lucide="log-out"></i>退出登录</button></div></div>`;
  });

  refreshIcons();
}

function getSafeAuthReturnPath() {
  return "01-research-entry.html";
}

function setAuthError(form, message) {
  const error = form.querySelector("[data-auth-error]");
  if (!error) return;
  error.textContent = message;
  error.hidden = !message;
}

renderAuthAreas();
renderStaticWorkspaceMenus();

// 工作区侧栏的折叠状态跨页面保存；移动端保留横向导航，不使用桌面图标栏模式。
const RAIL_COLLAPSED_STORAGE_KEY = "prototype-rail-collapsed";
const isCompactViewport = window.matchMedia("(max-width: 820px)").matches;

function setRailState(shell, collapsed, persist = true) {
  const toggle = shell.querySelector("[data-rail-toggle]");
  if (!toggle) return;

  const nextCollapsed = isCompactViewport ? false : collapsed;
  shell.classList.toggle("rail-collapsed", nextCollapsed);
  toggle.dataset.railState = nextCollapsed ? "collapsed" : "expanded";
  toggle.setAttribute("aria-expanded", String(!nextCollapsed));
  toggle.setAttribute("aria-label", nextCollapsed ? "展开左侧栏" : "收起左侧栏");
  toggle.title = nextCollapsed ? "展开左侧栏" : "收起左侧栏";

  if (persist && !isCompactViewport) {
    localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, String(nextCollapsed));
  }
}

document.querySelectorAll(".app-shell").forEach((shell) => {
  const initialCollapsed = localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY) === "true";
  setRailState(shell, initialCollapsed, false);
  shell.querySelector("[data-rail-toggle]")?.addEventListener("click", () => {
    setRailState(shell, !shell.classList.contains("rail-collapsed"));
  });
  shell.querySelectorAll(".workspace-nav-item").forEach((item) => {
    const title = item.querySelector(".workspace-nav-title")?.textContent?.trim();
    if (title) item.title = title;
  });
});

// 研究对话页只保存当前工作区内的会话侧栏状态，避免影响其他页面的工作区导航。
const CHAT_SIDEBAR_COLLAPSED_STORAGE_KEY = "prototype-chat-sidebar-collapsed";

function setChatSidebarState(shell, collapsed, persist = true) {
  const toggle = shell.querySelector("[data-chat-sidebar-toggle]");
  if (!toggle) return;

  const isCompact = window.matchMedia("(max-width: 820px)").matches;
  const nextCollapsed = isCompact ? false : collapsed;
  shell.classList.toggle("chat-sidebar-collapsed", nextCollapsed);
  toggle.dataset.chatSidebarState = nextCollapsed ? "collapsed" : "expanded";
  toggle.setAttribute("aria-expanded", String(!nextCollapsed));
  toggle.setAttribute("aria-label", nextCollapsed ? "展开研究会话侧栏" : "收起研究会话侧栏");
  toggle.title = nextCollapsed ? "展开研究会话侧栏" : "收起研究会话侧栏";

  if (persist) localStorage.setItem(CHAT_SIDEBAR_COLLAPSED_STORAGE_KEY, String(nextCollapsed));
}

document.querySelectorAll("[data-chat-app-shell]").forEach((shell) => {
  const initialCollapsed = localStorage.getItem(CHAT_SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
  setChatSidebarState(shell, initialCollapsed, false);
  shell.querySelector("[data-chat-sidebar-toggle]")?.addEventListener("click", () => {
    setChatSidebarState(shell, !shell.classList.contains("chat-sidebar-collapsed"));
  });
});

document.querySelectorAll("[data-chat-new-session]").forEach((button) => {
  button.addEventListener("click", () => showToast("正式接入后会以当前研究集合创建一段新对话"));
});

document.querySelectorAll("[data-chat-session-preview]").forEach((button) => {
  button.addEventListener("click", () => showToast(button.dataset.chatSessionPreview));
});

document.addEventListener("click", (event) => {
  const workspaceTrigger = event.target.closest("[data-workspace-switcher]");
  if (workspaceTrigger) {
    const menu = workspaceTrigger.parentElement?.querySelector("[data-workspace-menu]");
    const isOpen = workspaceTrigger.getAttribute("aria-expanded") === "true";
    document.querySelectorAll("[data-workspace-menu]").forEach((item) => { item.hidden = true; });
    document.querySelectorAll("[data-workspace-switcher]").forEach((item) => { item.setAttribute("aria-expanded", "false"); });
    if (menu) {
      menu.hidden = isOpen;
      if (!isOpen) {
        const search = menu.querySelector("[data-workspace-search]");
        if (search) {
          search.value = "";
          search.dispatchEvent(new Event("input", { bubbles: true }));
          window.setTimeout(() => search.focus(), 0);
        }
      }
    }
    workspaceTrigger.setAttribute("aria-expanded", String(!isOpen));
    return;
  }

  const accountTrigger = event.target.closest("[data-account-trigger]");
  if (accountTrigger) {
    const menu = accountTrigger.parentElement?.querySelector("[data-account-menu]");
    const isOpen = accountTrigger.getAttribute("aria-expanded") === "true";
    document.querySelectorAll("[data-account-menu]").forEach((item) => { item.hidden = true; });
    document.querySelectorAll("[data-account-trigger]").forEach((item) => { item.setAttribute("aria-expanded", "false"); });
    if (menu) menu.hidden = isOpen;
    accountTrigger.setAttribute("aria-expanded", String(!isOpen));
    return;
  }

  if (!event.target.closest(".account-menu-wrap")) {
    document.querySelectorAll("[data-account-menu]").forEach((item) => { item.hidden = true; });
    document.querySelectorAll("[data-account-trigger]").forEach((item) => { item.setAttribute("aria-expanded", "false"); });
  }

  if (!event.target.closest(".workspace-menu-wrap, .chat-workspace-menu-wrap")) {
    document.querySelectorAll("[data-workspace-menu]").forEach((item) => { item.hidden = true; });
    document.querySelectorAll("[data-workspace-switcher]").forEach((item) => { item.setAttribute("aria-expanded", "false"); });
  }

  if (event.target.closest("[data-account-settings]")) {
    showToast("账号设置页面将在正式前端中接入");
  }

  if (event.target.closest("[data-logout]")) {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    window.location.href = "01-research-entry.html";
  }
});

// 搜索只更新当前菜单的列表，避免一个页面上存在多个切换器时互相串台。
document.addEventListener("input", (event) => {
  const search = event.target.closest("[data-workspace-search]");
  if (!search) return;
  const menu = search.closest("[data-workspace-menu]");
  const list = menu?.querySelector("[data-workspace-menu-list]");
  if (!list) return;
  list.innerHTML = renderWorkspaceMenuList(getCurrentPrototypeWorkspaceId(), search.value);
  refreshIcons();
});

document.querySelector("[data-login-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const email = form.elements.email.value.trim();
  const password = form.elements.password.value;

  if (!email || password.length < 8) {
    setAuthError(form, "请输入有效邮箱和至少 8 位密码。");
    return;
  }

  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ name: "林岚", email }));
  window.location.href = getSafeAuthReturnPath();
});

document.querySelector("[data-register-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const name = form.elements.name.value.trim();
  const email = form.elements.email.value.trim();
  const password = form.elements.password.value;
  const passwordConfirm = form.elements["password-confirm"].value;

  if (!name || !email || password.length < 8) {
    setAuthError(form, "请完整填写昵称、邮箱和至少 8 位密码。");
    return;
  }
  if (password !== passwordConfirm) {
    setAuthError(form, "两次输入的密码不一致。");
    return;
  }

  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ name, email }));
  window.location.href = "01-research-entry.html";
});

const runner = document.querySelector("[data-runner]");

if (runner) {
  const requestInput = runner.querySelector("[data-research-request]");
  const requestError = runner.querySelector("[data-request-error]");
  const requestEchoes = runner.querySelectorAll("[data-request-echo]");
  const views = runner.querySelectorAll("[data-runner-view]");
  const analysisSteps = runner.querySelectorAll("[data-analysis-step]");
  const searchStages = runner.querySelectorAll("[data-search-stage]");
  const sourceStates = runner.querySelectorAll("[data-source-state]");
  const analysisMessage = runner.querySelector("[data-analysis-message]");
  const searchMessage = runner.querySelector("[data-search-message]");
  const literatureTimeRange = runner.querySelector("[data-literature-time-range]");
  const literatureLanguage = runner.querySelector("[data-literature-language]");
  const customDateRange = runner.querySelector("[data-custom-date-range]");
  const literatureStartYear = runner.querySelector("[data-literature-start-year]");
  const literatureEndYear = runner.querySelector("[data-literature-end-year]");
  const scopeError = runner.querySelector("[data-scope-error]");
  const currentYear = new Date().getFullYear();
  const timers = [];

  const analysisMessages = [
    "系统正在识别研究对象和需要核验的关系。",
    "系统正在判断影响因素、结果变量和比较维度。",
    "系统正在组合中英文概念组与检索表达。",
    "系统正在检查文献能否支撑后续全文研究。",
  ];
  const searchMessages = [
    "正在向已启用的文献源发送检索请求。",
    "正在将各来源的返回字段规整为统一记录。",
    "正在合并重复题录并核验引用信息。",
    "正在检查全文是否可获取和可解析。",
  ];

  function clearTimers() {
    while (timers.length) window.clearTimeout(timers.pop());
  }

  function schedule(callback, delay) {
    timers.push(window.setTimeout(callback, delay));
  }

  function setRunnerState(state) {
    runner.dataset.runnerState = state;
    views.forEach((view) => {
      const active = view.dataset.runnerView === state;
      view.hidden = !active;
      view.classList.toggle("is-active", active);
    });
  }

  function setRequestEcho(request) {
    requestEchoes.forEach((echo) => {
      echo.textContent = request;
    });
  }

  function resetStepStates(steps, stageLabels) {
    steps.forEach((step, index) => {
      step.classList.remove("is-active", "is-complete");
      const state = step.querySelector(".stage-state");
      if (state) state.textContent = stageLabels?.[index] ?? "等待";
    });
  }

  function setScopeError(message = "") {
    if (scopeError) {
      scopeError.textContent = message;
      scopeError.hidden = !message;
    }
    [literatureStartYear, literatureEndYear].forEach((input) => {
      input?.toggleAttribute("aria-invalid", Boolean(message));
    });
  }

  // 年份上限取浏览器当前年份，避免原型跨年后仍接受已过期的固定上限。
  function syncCustomDateRange() {
    const isCustomRange = literatureTimeRange?.value === "custom";
    if (customDateRange) customDateRange.hidden = !isCustomRange;
    [literatureStartYear, literatureEndYear].forEach((input) => {
      input?.setAttribute("max", String(currentYear));
    });
    runner.querySelectorAll("[data-current-year]").forEach((element) => { element.textContent = String(currentYear); });
    setScopeError();
  }

  function getLiteratureScope() {
    const language = literatureLanguage?.value ?? "中文和英文";
    const timeRange = literatureTimeRange?.value ?? "2019 年至今";
    if (timeRange !== "custom") return { language, timeRange };

    const startYear = Number(literatureStartYear?.value);
    const endYear = Number(literatureEndYear?.value);
    const yearsArePresent = Number.isInteger(startYear) && Number.isInteger(endYear);

    if (!yearsArePresent) {
      setScopeError("请填写起始年份和结束年份。");
      literatureStartYear?.focus();
      return null;
    }
    if (startYear < 1900 || endYear < 1900 || startYear > currentYear || endYear > currentYear) {
      setScopeError(`年份必须在 1900 至 ${currentYear} 年之间。`);
      return null;
    }
    if (startYear > endYear) {
      setScopeError("起始年份不能晚于结束年份。");
      literatureStartYear?.focus();
      return null;
    }

    return { language, timeRange: `${startYear} 至 ${endYear}` };
  }

  // 原型用定时事件模拟后台任务推送，让状态切换与未来的 SSE 事件模型保持一致。
  function runAnalysis() {
    clearTimers();
    resetStepStates(analysisSteps);

    analysisSteps.forEach((step, index) => {
      schedule(() => {
        step.classList.add("is-active");
        if (analysisMessage) analysisMessage.textContent = analysisMessages[index];
      }, index * 680);
      schedule(() => {
        step.classList.remove("is-active");
        step.classList.add("is-complete");
      }, index * 680 + 540);
    });

    schedule(() => setRunnerState("review"), analysisSteps.length * 680 + 140);
  }

  function runSearch() {
    clearTimers();
    resetStepStates(searchStages, Array(searchStages.length).fill("等待"));
    sourceStates.forEach((source) => source.classList.remove("is-active", "is-complete"));

    searchStages.forEach((stage, index) => {
      schedule(() => {
        stage.classList.add("is-active");
        const state = stage.querySelector(".stage-state");
        if (state) state.textContent = "处理中";
        if (searchMessage) searchMessage.textContent = searchMessages[index];
        sourceStates[index]?.classList.add("is-active");
      }, index * 720);
      schedule(() => {
        stage.classList.remove("is-active");
        stage.classList.add("is-complete");
        const state = stage.querySelector(".stage-state");
        if (state) state.textContent = "完成";
        sourceStates[index]?.classList.remove("is-active");
        sourceStates[index]?.classList.add("is-complete");
      }, index * 720 + 580);
    });

    schedule(() => setRunnerState("ready"), searchStages.length * 720 + 180);
  }

  runner.querySelector("[data-research-request-form]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const request = requestInput?.value.trim();
    if (!request) {
      requestError?.classList.add("show");
      requestInput?.focus();
      return;
    }

    requestError?.classList.remove("show");
    localStorage.setItem("prototype-research-request", request);
    setRequestEcho(request);
    setRunnerState("analyzing");
    runAnalysis();
  });

  runner.querySelectorAll("[data-direction]").forEach((option) => {
    option.addEventListener("click", () => {
      runner.querySelectorAll("[data-direction]").forEach((item) => {
        const selected = item === option;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-checked", String(selected));
      });
    });
  });

  literatureTimeRange?.addEventListener("change", syncCustomDateRange);

  runner.querySelector("[data-confirm-intent]")?.addEventListener("click", () => {
    const scope = getLiteratureScope();
    if (!scope) return;

    localStorage.setItem("prototype-literature-scope", JSON.stringify(scope));
    setRunnerState("searching");
    runSearch();
  });

  runner.querySelector("[data-restart-research]")?.addEventListener("click", () => {
    clearTimers();
    if (requestInput) requestInput.value = "";
    if (literatureTimeRange) literatureTimeRange.value = "2019 年至今";
    if (literatureLanguage) literatureLanguage.value = "中文和英文";
    if (literatureStartYear) literatureStartYear.value = "";
    if (literatureEndYear) literatureEndYear.value = "";
    syncCustomDateRange();
    requestError?.classList.remove("show");
    setRunnerState("idle");
    requestInput?.focus();
    showToast("已返回研究输入界面");
  });
}

// 阶段推进必须从当前阶段的明确动作触发，不能通过平级导航绕过。
document.querySelectorAll("[data-stage-advance]").forEach((button) => {
  button.addEventListener("click", () => {
    navigateWithFeedback(button, button.dataset.stageAdvance, "当前阶段已确认，正在准备下一步");
  });
});

// 集合构建是一个明确的确认动作，不能由“选择了若干文献”自动触发。
const collectionConfirmPanel = document.querySelector("[data-collection-confirm-panel]");

function setCollectionConfirmPanel(open) {
  if (!collectionConfirmPanel) return;
  collectionConfirmPanel.hidden = !open;
  document.body.classList.toggle("collection-confirm-open", open);
}

document.querySelectorAll("[data-collection-confirm]").forEach((button) => {
  button.addEventListener("click", () => {
    setCollectionConfirmPanel(true);
    collectionConfirmPanel?.querySelector("[data-collection-confirm-submit]")?.focus();
  });
});

document.querySelector("[data-collection-confirm-cancel]")?.addEventListener("click", () => {
  setCollectionConfirmPanel(false);
});

document.querySelector("[data-collection-confirm-submit]")?.addEventListener("click", (event) => {
  localStorage.setItem("prototype-collection-build", "indexing");
  const workspace = getPrototypeWorkspace() ?? "sleep";
  navigateWithFeedback(event.currentTarget, `04-research-workspace.html?workspace=${workspace}&collection=building`, "已确认文献集合，正在启动索引任务");
});

// 详情页的集合动作会保留为待确认选择，直到用户在结果页确认构建集合。
const COLLECTION_SELECTION_STORAGE_KEY = "prototype-collection-selection";

function getPrototypeWorkspace() {
  const workspace = new URLSearchParams(window.location.search).get("workspace");
  return workspace === "sleep" || workspace === "community" ? workspace : null;
}

function getCollectionSelectionStorageKey() {
  // 待确认集合只属于一个工作区，静态原型也不能让不同研究任务共用选择状态。
  return `${COLLECTION_SELECTION_STORAGE_KEY}-${getPrototypeWorkspace() ?? "default"}`;
}

function getCollectionSelection() {
  try {
    const selection = JSON.parse(localStorage.getItem(getCollectionSelectionStorageKey()) ?? "[]");
    return Array.isArray(selection) ? selection : [];
  } catch {
    return [];
  }
}

function setCollectionSelection(selection) {
  localStorage.setItem(getCollectionSelectionStorageKey(), JSON.stringify(selection));
}

document.querySelectorAll("[data-toggle-collection]").forEach((button) => {
  const paperId = button.dataset.paperId;
  if (!paperId) return;

  function renderCollectionSelection() {
    const selected = getCollectionSelection().includes(paperId);
    const label = button.querySelector("[data-toggle-collection-label]");
    const icon = button.querySelector("svg");
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    if (label) label.textContent = selected ? "已加入待确认集合" : "加入研究集合";
    if (icon) icon.outerHTML = `<i data-lucide="${selected ? "check" : "plus"}"></i>`;
    refreshIcons();
  }

  renderCollectionSelection();
  button.addEventListener("click", () => {
    const selection = getCollectionSelection();
    const nextSelection = selection.includes(paperId)
      ? selection.filter((id) => id !== paperId)
      : [...selection, paperId];
    setCollectionSelection(nextSelection);
    renderCollectionSelection();
    showToast(nextSelection.includes(paperId) ? "已加入待确认集合" : "已从待确认集合移除");
  });
});

// 集合页仅在可从当前链接确认了构建任务时演示索引状态，直接进入该页则保持已就绪样例。
function setCollectionEntryAvailability(available) {
  document.querySelectorAll("[data-collection-entry]").forEach((entry) => {
    if (available) {
      entry.classList.remove("is-disabled");
      entry.removeAttribute("aria-disabled");
      entry.removeAttribute("tabindex");
      return;
    }

    entry.classList.add("is-disabled");
    entry.setAttribute("aria-disabled", "true");
    entry.setAttribute("tabindex", "-1");
  });
}

document.querySelectorAll("[data-collection-entry]").forEach((entry) => {
  entry.addEventListener("click", (event) => {
    if (entry.getAttribute("aria-disabled") !== "true") return;
    event.preventDefault();
    showToast("研究集合仍在构建，完成索引后才能开始对话");
  });
});

function runCollectionBuildPrototype() {
  const buildStatus = document.querySelector("[data-collection-build-status]");
  if (!buildStatus) return;

  const buildTitle = buildStatus.querySelector("[data-collection-build-title]");
  const buildMessage = buildStatus.querySelector("[data-collection-build-message]");
  const readyBadge = document.querySelector("[data-collection-ready]");
  const heading = document.querySelector("[data-collection-heading]");
  const description = document.querySelector("[data-collection-description]");
  const documentTitle = document.querySelector("[data-collection-document-title]");
  const readyCount = document.querySelector("[data-collection-ready-count]");
  const readyLabel = document.querySelector("[data-collection-ready-label]");

  buildStatus.hidden = false;
  setCollectionEntryAvailability(false);
  if (readyBadge) readyBadge.innerHTML = '<i data-lucide="loader-circle"></i>集合构建中';
  if (heading) heading.textContent = "正在准备可追溯的研究集合。";
  if (description) description.textContent = "已确认文献正在经历全文解析、片段切分与向量索引。构建完成前，研究对话不会引用它们。";
  if (documentTitle) documentTitle.textContent = "8 篇文献正在构建可问答索引";
  if (readyCount) readyCount.textContent = "0";
  if (readyLabel) readyLabel.textContent = "已完成索引";
  refreshIcons();

  window.setTimeout(() => {
    if (buildTitle) buildTitle.textContent = "正在解析全文与引用位置";
    if (buildMessage) buildMessage.textContent = "已验证 PDF 可读性，正在生成可定位的文本片段。";
  }, 700);

  window.setTimeout(() => {
    if (buildTitle) buildTitle.textContent = "正在写入当前工作区索引";
    if (buildMessage) buildMessage.textContent = "片段将只属于当前工作区，后续问答不会跨集合检索。";
  }, 1450);

  window.setTimeout(() => {
    if (buildTitle) buildTitle.textContent = "研究集合已准备好";
    if (buildMessage) buildMessage.textContent = "8 篇全文已完成索引，可以开始提出研究问题。";
    if (readyBadge) readyBadge.innerHTML = '<i data-lucide="shield-check"></i>索引状态正常';
    if (heading) heading.textContent = "研究集合已经准备好。";
    if (description) description.textContent = "当前集合中的全文已经完成索引。进入研究对话后，每条结论都需要引用可定位的原文证据。";
    if (documentTitle) documentTitle.textContent = "8 篇文献可用于问答";
    if (readyCount) readyCount.textContent = "8";
    if (readyLabel) readyLabel.textContent = "已完成索引";
    localStorage.setItem("prototype-collection-build", "ready");
    setCollectionEntryAvailability(true);
    refreshIcons();
  }, 2250);
}

if (new URLSearchParams(window.location.search).get("collection") === "building") {
  runCollectionBuildPrototype();
}

const WORKSPACE_SAMPLES = {
  sleep: {
    name: "睡眠与大学生心理健康",
    topic: "睡眠质量与大学生心理健康",
    navigationState: "候选文献核验中",
    sessionTitle: "睡眠质量与心理健康的关系",
    documents: [
      ["Sleep quality and depressive symptoms among university students: A longitudinal study", "Chen Y, Wu M · 2024 · Journal of American College Health", "48 片段"],
      ["Sleep duration, sleep quality, and anxiety symptoms in college students", "Liu J, Park S · 2023 · Sleep Health", "52 片段"],
      ["Social media use, sleep disturbance, and psychological distress among undergraduates", "Zhang L, et al. · 2022 · BMC Public Health", "41 片段"],
    ],
  },
  community: {
    name: "社区协同设计与信任",
    topic: "社区协同设计如何影响公共项目的信任",
    navigationState: "候选文献核验中",
    sessionTitle: "协同设计如何影响公共项目信任",
    documents: [
      ["Co-design and citizen trust in public development projects", "Lopez A, Chen M · 2024 · Journal of Public Deliberation", "44 片段"],
      ["Participation depth and procedural fairness in neighborhood planning", "Ahmed R, Wu Y · 2023 · Planning Theory & Practice", "39 片段"],
      ["Feedback loops in public consultation and institutional trust", "Kim S, Patel N · 2022 · Policy Studies Journal", "46 片段"],
    ],
    resultPapers: [
      {
        title: "Co-design and citizen trust in public development projects",
        topic: "公共项目协同设计 · 英文",
        author: "Lopez A, Chen M",
        year: "2024",
        venue: "Journal of Public Deliberation",
        source: "OpenAlex + Crossref",
        state: "题录已核验，全文可用。它直接考察协同设计参与与公众信任的关联，可作为当前研究集合的核心证据。",
        citation: "Lopez A, Chen M. Co-design and citizen trust in public development projects[J]. Journal of Public Deliberation, 2024.",
      },
      {
        title: "Participation depth and procedural fairness in neighborhood planning",
        topic: "比较性案例研究 · 英文",
        author: "Ahmed R, Wu Y",
        year: "2023",
        venue: "Planning Theory & Practice",
        source: "Semantic Scholar",
        state: "题录已核验，全文正在处理。",
        citation: "Ahmed R, Wu Y. Participation depth and procedural fairness in neighborhood planning[J]. Planning Theory & Practice, 2023.",
      },
      {
        title: "Feedback loops in public consultation and institutional trust",
        topic: "反馈机制研究 · 英文",
        author: "Kim S, Patel N",
        year: "2022",
        venue: "Policy Studies Journal",
        source: "OpenAlex",
        state: "题录已核验，暂未找到可处理全文。",
        citation: "Kim S, Patel N. Feedback loops in public consultation and institutional trust[J]. Policy Studies Journal, 2022.",
      },
      {
        title: "社区协同设计中的反馈机制与公共信任",
        topic: "社区参与研究 · 中文",
        author: "王晨, 刘宁",
        year: "2024",
        venue: "公共管理学报",
        source: "Crossref",
        state: "题录仍在补全，暂不能入集合。",
        citation: "王晨, 刘宁. 社区协同设计中的反馈机制与公共信任[J]. 公共管理学报, 2024.",
      },
    ],
    paper: {
      title: "Co-design and citizen trust in public development projects",
      byline: "Lopez A, Chen M · 2024 · Journal of Public Deliberation",
      doi: "10.0000/academic-search.community",
      design: "多案例比较研究",
      subject: "公共项目参与者",
      topics: "协同设计、公众信任",
      summary: "这项案例比较研究考察了公共项目中不同协同设计参与深度与公众信任的关系。它可以支持讨论参与程序与信任之间的关联，但仍需结合项目背景和研究设计判断因果解释。",
      abstract: "This comparative study examined how co-design participation and feedback mechanisms were associated with citizen trust across public development projects.",
      fit: "论文直接对应“社区协同设计如何影响公共项目的信任”，且具备 DOI、可核验题录和可处理全文，符合当前工作区的固定准入规则。",
      evidence: "较深的参与和可见的反馈闭环，与参与者更高的程序公平感和公共项目信任相关。",
      evidenceLocation: "结果段落 · 原型演示片段 18",
      citationPreviews: {
        gbt: { label: "GB/T 7714-2015", text: "Lopez A, Chen M. Co-design and citizen trust in public development projects[J]. Journal of Public Deliberation, 2024. DOI: 10.0000/academic-search.community." },
        apa: { label: "APA 7", text: "Lopez, A., & Chen, M. (2024). Co-design and citizen trust in public development projects. Journal of Public Deliberation. https://doi.org/10.0000/academic-search.community" },
        mla: { label: "MLA 9", text: "Lopez, A., and M. Chen. \"Co-design and Citizen Trust in Public Development Projects.\" Journal of Public Deliberation, 2024, doi:10.0000/academic-search.community." },
        chicago: { label: "Chicago author-date", text: "Lopez, A., and M. Chen. 2024. \"Co-design and Citizen Trust in Public Development Projects.\" Journal of Public Deliberation. https://doi.org/10.0000/academic-search.community." },
        bibtex: { label: "BibTeX", text: "@article{citation_academic_search_community,\n  author = {Lopez, A. and Chen, M.},\n  title = {Co-design and citizen trust in public development projects},\n  journal = {Journal of Public Deliberation},\n  year = {2024},\n  doi = {10.0000/academic-search.community}\n}" },
      },
    },
  },
};

function applyWorkspaceSampleContext() {
  const workspace = getPrototypeWorkspace();
  if (!workspace) return;
  const sample = WORKSPACE_SAMPLES[workspace];

  document.querySelectorAll(".workspace-switcher span, .workspace-nav-item.active .workspace-nav-title").forEach((element) => {
    element.textContent = sample.name;
  });
  document.querySelectorAll(".workspace-nav-item.active .workspace-nav-meta").forEach((element) => {
    element.textContent = sample.navigationState;
  });
  document.querySelectorAll(".workspace-subline span").forEach((element) => {
    element.textContent = sample.topic;
  });

  // 同一工作区中的内部跳转始终携带样例身份，正式版改为路由中的工作区 ID。
  const contextualRoutes = new Set([
    "03-literature-results.html",
    "04-research-workspace.html",
    "08-paper-detail.html",
    "09-research-chat.html",
  ]);
  document.querySelectorAll("a[href]").forEach((link) => {
    const route = link.getAttribute("href");
    const workspaceItem = link.closest(".workspace-nav-item");
    if (workspaceItem && !workspaceItem.classList.contains("active")) return;
    if (contextualRoutes.has(route)) link.setAttribute("href", `${route}?workspace=${workspace}`);
  });

  const collectionDocuments = document.querySelectorAll(".collection-document:not(.more)");
  collectionDocuments.forEach((document, index) => {
    const documentSample = sample.documents[index];
    if (!documentSample) return;
    const title = document.querySelector("strong");
    const metadata = document.querySelector("small");
    const state = document.querySelector(".document-state");
    if (title) title.textContent = documentSample[0];
    if (metadata) metadata.textContent = documentSample[1];
    if (state) state.textContent = documentSample[2];
  });

  if (sample.resultPapers) {
    document.querySelectorAll("[data-paper]").forEach((row, index) => {
      const paper = sample.resultPapers[index];
      if (!paper) return;
      row.dataset.paperTitle = paper.title;
      row.dataset.paperMeta = `${paper.author} · ${paper.year} · ${paper.venue}`;
      row.dataset.paperSource = paper.source;
      row.dataset.paperState = paper.state;
      const cells = row.querySelectorAll("td");
      const title = row.querySelector(".paper-name");
      const topic = cells[0]?.querySelector(".paper-meta");
      const year = cells[1]?.querySelector(".paper-meta");
      const venue = cells[2]?.querySelector("strong");
      const source = cells[2]?.lastChild;
      const copyButton = row.querySelector("[data-copy]");
      if (title) title.textContent = paper.title;
      if (topic) topic.textContent = paper.topic;
      if (cells[1]) cells[1].firstChild.textContent = paper.author;
      if (year) year.textContent = paper.year;
      if (venue) venue.textContent = paper.venue;
      if (source?.nodeType === Node.TEXT_NODE) source.textContent = paper.source;
      if (copyButton) copyButton.dataset.copy = paper.citation;
    });

    const firstPaper = sample.resultPapers[0];
    if (inspectorTitle) inspectorTitle.textContent = firstPaper.title;
    if (inspectorMeta) inspectorMeta.innerHTML = `${firstPaper.author} · ${firstPaper.year} · ${firstPaper.venue}<br />来源：${firstPaper.source}`;
    if (inspectorState) inspectorState.textContent = firstPaper.state;
  }

  document.querySelectorAll(".chat-workspace-copy strong, .chat-current-workspace span").forEach((element) => {
    element.textContent = sample.name;
  });
  const chatTitle = document.querySelector(".research-chat-identity h1");
  if (chatTitle) chatTitle.textContent = sample.topic;
  const activeSession = document.querySelector(".chat-session-item.active .chat-session-copy strong");
  if (activeSession) activeSession.textContent = sample.sessionTitle;
  const welcome = document.querySelector(".chat-welcome-message p");
  if (welcome) welcome.textContent = `当前对话只检索${sample.name}工作区中已完成索引的全文。提出问题后，系统会先展示可验证的检索执行状态。`;
  document.querySelectorAll(".chat-thread > .chat-message:not(.chat-welcome-message)").forEach((message) => {
    message.hidden = true;
  });

  if (!sample.paper) return;
  const paper = sample.paper;
  const paperTitle = document.querySelector("[data-context-paper-title]");
  const paperByline = document.querySelector("[data-context-paper-byline]");
  const paperSummary = document.querySelector("[data-context-paper-summary]");
  const paperAbstract = document.querySelector("[data-context-paper-abstract]");
  const paperDoi = document.querySelector("[data-context-paper-doi]");
  const paperDesign = document.querySelector("[data-context-paper-design]");
  const paperSubject = document.querySelector("[data-context-paper-subject]");
  const paperTopics = document.querySelector("[data-context-paper-topics]");
  const paperFit = document.querySelector("[data-context-paper-fit]");
  const paperEvidence = document.querySelector("[data-context-paper-evidence]");
  const paperEvidenceLocation = document.querySelector("[data-context-paper-evidence-location]");
  if (paperTitle) paperTitle.textContent = paper.title;
  if (paperByline) paperByline.textContent = paper.byline;
  if (paperSummary) paperSummary.textContent = paper.summary;
  if (paperAbstract) paperAbstract.textContent = paper.abstract;
  if (paperDoi) {
    paperDoi.textContent = paper.doi;
    paperDoi.setAttribute("href", `https://doi.org/${paper.doi}`);
  }
  if (paperDesign) paperDesign.textContent = paper.design;
  if (paperSubject) paperSubject.textContent = paper.subject;
  if (paperTopics) paperTopics.textContent = paper.topics;
  if (paperFit) paperFit.textContent = paper.fit;
  if (paperEvidence) paperEvidence.textContent = paper.evidence;
  if (paperEvidenceLocation) paperEvidenceLocation.textContent = paper.evidenceLocation;
  document.querySelectorAll("[data-context-workspace-name]").forEach((element) => {
    element.textContent = sample.name;
  });
  Object.assign(citationFormatPreviews, paper.citationPreviews);
  renderCitationFormat(selectedCitationFormat);
}

document.querySelectorAll("[data-edit-spec]").forEach((button) => {
  button.addEventListener("click", () => {
    const field = button.closest(".field-cell");
    field?.classList.toggle("editing");
    showToast(field?.classList.contains("editing") ? "字段已切换为可编辑状态" : "字段编辑状态已收起");
  });
});

document.querySelectorAll("[data-show-toast]").forEach((button) => {
  button.addEventListener("click", () => showToast(button.dataset.showToast));
});

document.querySelectorAll("[data-show-processing]").forEach((button) => {
  button.addEventListener("click", () => {
    const detail = document.querySelector("[data-processing-detail]");
    const isVisible = detail?.classList.toggle("show");
    showToast(isVisible ? "已展开本次文献处理记录" : "已收起处理记录");
  });
});

document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-filter]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    showToast(`已切换到${button.textContent.trim()}筛选`);
  });
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
    } catch {
      // 文件协议或浏览器权限限制剪贴板时，原型仍需保留可感知的操作结果。
    }
    showToast("GB/T 7714 题录已复制");
  });
});

// 详情页模拟后端统一题录格式化接口的输出；正式版只接收格式键和已渲染文本。
const citationFormatPreviews = {
  gbt: {
    label: "GB/T 7714-2015",
    text: "Chen Y, Wu M. Sleep quality and depressive symptoms among university students: A longitudinal study[J]. Journal of American College Health, 2024. DOI: 10.0000/academic-search.prototype.",
  },
  apa: {
    label: "APA 7",
    text: "Chen, Y., & Wu, M. (2024). Sleep quality and depressive symptoms among university students: A longitudinal study. Journal of American College Health. https://doi.org/10.0000/academic-search.prototype",
  },
  mla: {
    label: "MLA 9",
    text: "Chen, Y., and M. Wu. \"Sleep Quality and Depressive Symptoms among University Students: A Longitudinal Study.\" Journal of American College Health, 2024, doi:10.0000/academic-search.prototype.",
  },
  chicago: {
    label: "Chicago author-date",
    text: "Chen, Y., and M. Wu. 2024. \"Sleep Quality and Depressive Symptoms among University Students: A Longitudinal Study.\" Journal of American College Health. https://doi.org/10.0000/academic-search.prototype.",
  },
  bibtex: {
    label: "BibTeX",
    text: "@article{citation_10_0000_academic_search_prototype,\n  author = {Chen, Y. and Wu, M.},\n  title = {Sleep quality and depressive symptoms among university students: A longitudinal study},\n  journal = {Journal of American College Health},\n  year = {2024},\n  doi = {10.0000/academic-search.prototype}\n}",
  },
};

const citationControl = document.querySelector("[data-citation-control]");
const citationPanel = document.querySelector("[data-citation-panel]");
const citationControlRoot = citationControl?.closest(".citation-control");
const citationPreview = document.querySelector("[data-citation-preview]");
const citationCopyButton = document.querySelector("[data-citation-copy]");
const citationCopyLabel = document.querySelector("[data-citation-copy-label]");
let selectedCitationFormat = "gbt";

function renderCitationFormat(format) {
  const citation = citationFormatPreviews[format];
  if (!citation) return;

  selectedCitationFormat = format;
  if (citationPreview) citationPreview.textContent = citation.text;
  if (citationCopyLabel) citationCopyLabel.textContent = `复制 ${citation.label}`;

  document.querySelectorAll("[data-citation-format]").forEach((option) => {
    const active = option.dataset.citationFormat === format;
    option.classList.toggle("is-active", active);
    option.setAttribute("aria-selected", String(active));
  });
}

function setCitationPanel(open) {
  if (!citationControl || !citationControlRoot || !citationPanel) return;
  citationControlRoot.classList.toggle("is-open", open);
  citationControl.setAttribute("aria-expanded", String(open));
  citationPanel.hidden = !open;
}

citationControl?.addEventListener("click", () => {
  setCitationPanel(citationPanel?.hidden ?? true);
});

document.querySelectorAll("[data-citation-format]").forEach((option) => {
  option.addEventListener("click", () => renderCitationFormat(option.dataset.citationFormat));
});

citationCopyButton?.addEventListener("click", async () => {
  const citation = citationFormatPreviews[selectedCitationFormat];
  if (!citation) return;

  try {
    await navigator.clipboard.writeText(citation.text);
  } catch {
    // 文件协议或浏览器权限限制剪贴板时，原型仍需保留可感知的操作结果。
  }
  showToast(`${citation.label} 引用已复制`);
});

document.addEventListener("click", (event) => {
  if (citationControlRoot && !citationControlRoot.contains(event.target)) setCitationPanel(false);
});

const inspectorTitle = document.querySelector("[data-inspector-title]");
const inspectorMeta = document.querySelector("[data-inspector-meta]");
const inspectorState = document.querySelector("[data-inspector-state]");

document.querySelectorAll("[data-paper]").forEach((row) => {
  row.addEventListener("click", () => {
    document.querySelectorAll("[data-paper]").forEach((item) => item.classList.remove("selected"));
    row.classList.add("selected");
    if (inspectorTitle) inspectorTitle.textContent = row.dataset.paperTitle;
    if (inspectorMeta) inspectorMeta.innerHTML = `${row.dataset.paperMeta}<br />来源：${row.dataset.paperSource}`;
    if (inspectorState) inspectorState.textContent = row.dataset.paperState;
    showToast("已切换候选文献检查器");
  });
});

// 候选列表保留单击检查器；右侧按钮进入独立详情页，模拟正式版的路由入口。
document.querySelectorAll("[data-paper] .row-actions .icon-button:last-child").forEach((button) => {
  button.setAttribute("data-tooltip", "查看论文详情");
  button.setAttribute("aria-label", "查看论文详情");
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const workspace = getPrototypeWorkspace();
    window.location.href = workspace ? `08-paper-detail.html?workspace=${workspace}` : "08-paper-detail.html";
  });
});

// 详情页的标签只切换同一篇论文的展示层，避免把阅读位置带离当前记录。
document.querySelectorAll("[data-paper-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.paperTab;
    document.querySelectorAll("[data-paper-tab]").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-paper-panel]").forEach((panel) => {
      const active = panel.dataset.paperPanel === target;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
  });
});

const citationDetails = {
  1: {
    title: "引用 [1] · 绿地可达性与老年步行",
    meta: "Brown J, et al. · 2022<br />Landscape and Urban Planning",
    excerpt: "Higher neighborhood greenery was associated with more frequent walking among older adults, particularly where paths were perceived as safe.",
  },
  2: {
    title: "引用 [2] · 绿地环境与主观健康",
    meta: "Klein M, Roberts A · 2021<br />Health & Place",
    excerpt: "Accessible green space was associated with improved self-rated health after accounting for neighborhood socioeconomic conditions.",
  },
  3: {
    title: "引用 [3] · 步行作为可能机制",
    meta: "Liu Y, et al. · 2023<br />Journal of Aging and Physical Activity",
    excerpt: "Walking frequency partly explained the association between neighborhood greenery and psychological wellbeing among community-dwelling older adults.",
  },
  4: {
    title: "引用 [4] · 安全感与环境使用",
    meta: "Garcia R, Chen L · 2020<br />Urban Studies",
    excerpt: "Perceived safety and seating availability shaped whether nearby green spaces were used by older residents.",
  },
};

document.querySelectorAll("[data-citation]").forEach((button) => {
  button.addEventListener("click", () => {
    const citation = citationDetails[button.dataset.citation];
    if (!citation) return;
    const title = document.querySelector("[data-citation-title]");
    const meta = document.querySelector("[data-citation-meta]");
    const excerpt = document.querySelector("[data-citation-excerpt]");
    if (title) title.textContent = citation.title;
    if (meta) meta.innerHTML = citation.meta;
    if (excerpt) excerpt.textContent = citation.excerpt;
    showToast("已定位引用的原文证据");
  });
});

document.querySelector("[data-ask-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const input = event.currentTarget.querySelector("input");
  if (!input.value.trim()) {
    input.setAttribute("aria-invalid", "true");
    showToast("请先输入需要核验的问题");
    input.focus();
    return;
  }
  input.removeAttribute("aria-invalid");
  showToast("已在当前工作区的全文集合中提交检索");
});

// 文内标号只定位到本条回答的证据，不再让用户在常驻检查器和对话之间跳转。
document.querySelectorAll("[data-chat-evidence-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.chatEvidenceTarget);
    const details = target?.closest("details");
    if (!target || !details) return;

    details.open = true;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("is-highlighted");
    window.setTimeout(() => target.classList.remove("is-highlighted"), 1500);
  });
});

function scrollResearchChatToLatest() {
  const scrollRegion = document.querySelector(".chat-scroll-region");
  if (scrollRegion) scrollRegion.scrollTop = scrollRegion.scrollHeight;
}

function appendResearchChatQuestion(question) {
  const thread = document.querySelector("[data-research-chat-thread]");
  if (!thread) return;

  const message = document.createElement("article");
  message.className = "chat-message user-message";
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("strong");
  author.textContent = "你";
  const label = document.createElement("span");
  label.textContent = "刚刚提问";
  const text = document.createElement("p");
  text.textContent = question;

  meta.append(author, label);
  content.append(meta, text);
  message.append(content);
  thread.append(message);
  scrollResearchChatToLatest();
}

// 原型只展示可验证的执行状态，不为用户的新问题伪造研究结论或引用。
function appendResearchChatExecution() {
  const thread = document.querySelector("[data-research-chat-thread]");
  if (!thread) return null;

  const message = document.createElement("article");
  message.className = "chat-message assistant-message chat-execution-message";
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = '<i data-lucide="search-check"></i>';
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("strong");
  author.textContent = "研究助理";
  const stage = document.createElement("span");
  stage.textContent = "正在核验证据";
  const text = document.createElement("p");
  text.textContent = "正在当前集合中检索与问题直接相关的原文片段。";

  meta.append(author, stage);
  content.append(meta, text);
  message.append(avatar, content);
  thread.append(message);
  refreshIcons();
  scrollResearchChatToLatest();
  return { message, stage, text };
}

// 原型用有限状态模拟一次受控检索；正式版由服务端的稳定执行状态驱动相同反馈。
document.querySelector("[data-research-chat-form]")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const input = form.querySelector("[data-research-chat-input]");
  const status = form.querySelector("[data-research-chat-status]");
  const submit = form.querySelector("button[type='submit']");
  if (!input?.value.trim()) {
    input?.setAttribute("aria-invalid", "true");
    showToast("请先输入需要在当前集合中核验的问题");
    input?.focus();
    return;
  }

  input.removeAttribute("aria-invalid");
  const question = input.value.trim();
  appendResearchChatQuestion(question);
  const execution = appendResearchChatExecution();
  localStorage.setItem("prototype-last-research-question", question);
  input.value = "";
  input.style.height = "auto";
  form.classList.add("is-running");
  submit?.setAttribute("disabled", "true");
  if (status) status.textContent = "正在进行混合检索、父块合并、精排和证据核验。";
  window.setTimeout(() => {
    if (execution) {
      execution.stage.textContent = "正在合并上下文";
      execution.text.textContent = "已定位候选片段，正在合并它们的原始上下文以避免断章取义。";
    }
  }, 420);
  window.setTimeout(() => {
    if (execution) {
      execution.stage.textContent = "正在精排与核验";
      execution.text.textContent = "正在保留可直接支持结论的证据，并检查页码或段落定位。";
    }
  }, 880);
  window.setTimeout(() => {
    form.classList.remove("is-running");
    submit?.removeAttribute("disabled");
    if (execution) {
      execution.stage.textContent = "执行状态演示完成";
      execution.text.textContent = "原型不生成新的研究结论。正式版只有在证据核验通过后，才会在这里写入带引用的回答。";
    }
    if (status) status.textContent = "本次原型已展示执行状态；正式版会在证据核验后写入带引用的回答。";
    showToast("当前集合的证据检索状态已完成");
    scrollResearchChatToLatest();
  }, 1380);
});

// 输入框按内容增高，保持底部输入器紧凑，同时保留多行追问能力。
document.querySelector("[data-research-chat-input]")?.addEventListener("input", (event) => {
  const input = event.currentTarget;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
});

document.querySelector("[data-research-chat-input]")?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  event.currentTarget.form?.requestSubmit();
});

applyWorkspaceSampleContext();
