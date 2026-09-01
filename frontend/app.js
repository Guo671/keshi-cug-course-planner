"use strict";

const state = {
  token: localStorage.getItem("cugPlannerToken"),
  authMode: "login",
  profile: null,
  catalog: null,
  inputMode: "manual",
  selectedCourses: new Map(),
  courseDetails: new Map(),
  courseDetailRequests: new Map(),
  blockedTimes: [],
  instructorRules: [],
  curriculumPreview: null,
  planResponse: null,
  historyRuns: [],
  currentResultMeta: null,
  draftRestoreInProgress: false,
  draftSaveTimer: null,
  draftWriteBlocked: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const weekdayNames = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const PLANNING_RESULT_LIMIT = 10;

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  initializeStepNavigation();
  if (state.token) bootstrapApp();
  else showAuth();
});

function bindEvents() {
  $$('[data-auth-mode]').forEach((button) => button.addEventListener("click", () => setAuthMode(button.dataset.authMode)));
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#logout-button").addEventListener("click", logout);
  $("#profile-form").addEventListener("submit", saveProfile);
  $("#profile-cohort").addEventListener("change", updateSemesterNote);
  $("#selection-phase").addEventListener("change", updateRetakeConfirmation);
  $$('input[name="input-mode"]').forEach((input) => input.addEventListener("change", setInputMode));
  $("#course-search-button").addEventListener("click", searchCourses);
  $("#course-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); searchCourses(); } });
  $("#clear-courses").addEventListener("click", () => { state.selectedCourses.clear(); renderSelectedCourses(); scheduleDraftSave(); });
  $("#blocked-time-form").addEventListener("submit", addBlockedTime);
  $("#teacher-rule-form").addEventListener("submit", addTeacherRule);
  $("#refresh-curriculum").addEventListener("click", matchCurriculum);
  $("#import-curriculum").addEventListener("click", importCurriculumCourses);
  $("#include-optional").addEventListener("change", toggleOptionalCurriculumCourses);
  $("#generate-plan").addEventListener("click", generatePlan);
  $("#refresh-history").addEventListener("click", loadPlanningHistory);
  document.addEventListener("click", handleDelegatedClick);
  document.addEventListener("change", handleDelegatedChange);
  document.addEventListener("keydown", handleDelegatedKeydown);
}

async function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && !path.includes("/auth/")) {
    clearSession();
    showAuth();
    throw new Error("登录已失效，请重新登录");
  }
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join("；") : `请求失败（${response.status}）`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

function setAuthMode(mode) {
  state.authMode = mode;
  $$('[data-auth-mode]').forEach((button) => {
    const selected = button.dataset.authMode === mode;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $("#auth-submit").textContent = mode === "login" ? "登录" : "创建账户并登录";
  $("#auth-title").textContent = mode === "login" ? "登录本地账户" : "创建本地账户";
  $("#auth-password").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("#auth-error").textContent = "";
}

function initializeStepNavigation() {
  const steps = [
    ["profile", $("#profile-step")],
    ["courses", $("#course-step")],
    ["constraints", $("#constraint-step")],
    ["results", $("#result-step")],
  ];
  let scheduled = false;
  const update = () => {
    scheduled = false;
    const marker = Math.min(220, window.innerHeight * 0.3);
    let active = steps[0][0];
    steps.forEach(([name, section]) => {
      if (section?.getBoundingClientRect().top <= marker) active = name;
    });
    $$('[data-step-link], [data-compact-step]').forEach((link) => {
      const name = link.dataset.stepLink || link.dataset.compactStep;
      const current = name === active;
      link.classList.toggle("active", current);
      if (current) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    });
  };
  const scheduleUpdate = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(update);
  };
  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  new MutationObserver(scheduleUpdate).observe($("#app-shell"), { attributes: true, attributeFilter: ["class"] });
  update();
}

async function submitAuth(event) {
  event.preventDefault();
  const button = $("#auth-submit");
  button.disabled = true;
  $("#auth-error").textContent = "";
  try {
    const result = await api(`/api/auth/${state.authMode}`, {
      method: "POST",
      body: JSON.stringify({ username: $("#auth-username").value, password: $("#auth-password").value }),
    });
    resetUserState();
    state.token = result.access_token;
    localStorage.setItem("cugPlannerToken", state.token);
    await bootstrapApp();
  } catch (error) {
    $("#auth-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function bootstrapApp() {
  try {
    const me = await api("/api/auth/me");
    $("#current-user").textContent = me.username;
    await Promise.all([loadProfile(), loadCatalogStatus()]);
    await Promise.all([restorePlanningDraft(), loadPlanningHistory()]);
    $("#auth-screen").classList.add("hidden");
    $("#app-shell").classList.remove("hidden");
  } catch (error) {
    if (!state.token) return;
    toast(error.message, true);
  }
}

function showAuth() {
  $("#app-shell").classList.add("hidden");
  $("#auth-screen").classList.remove("hidden");
  // A user can log out from the result section far below the top of the page.
  // Reset the document before revealing the shorter auth screen so its logo
  // and heading are never rendered above the mobile viewport.
  window.scrollTo(0, 0);
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) { /* local token is cleared regardless */ }
  clearSession();
  showAuth();
}

function clearSession() {
  state.token = null;
  localStorage.removeItem("cugPlannerToken");
  resetUserState();
}

function resetUserState() {
  clearTimeout(state.draftSaveTimer);
  state.profile = null;
  state.catalog = null;
  state.inputMode = "manual";
  state.selectedCourses = new Map();
  state.courseDetails = new Map();
  state.courseDetailRequests = new Map();
  state.blockedTimes = [];
  state.instructorRules = [];
  state.curriculumPreview = null;
  state.planResponse = null;
  state.historyRuns = [];
  state.currentResultMeta = null;
  state.draftRestoreInProgress = false;
  state.draftSaveTimer = null;
  state.draftWriteBlocked = false;
  $("#profile-form")?.reset();
  $("#blocked-time-form")?.reset();
  $("#teacher-rule-form")?.reset();
  $("#current-user").textContent = "";
  $("#course-search").value = "";
  $("#course-search-results").innerHTML = '<div class="empty-state compact"><span>⌕</span><p>输入课程名称或课程号开始搜索</p></div>';
  $("#curriculum-course-list").innerHTML = "";
  $("#curriculum-evidence").textContent = "请先保存学生信息。";
  $("#curriculum-status").textContent = "等待匹配";
  $("#curriculum-status").className = "badge neutral";
  $("#curriculum-panel").classList.add("hidden");
  $("#confirm-semester").checked = false;
  $("#include-optional").checked = false;
  $("#retake-confirm").checked = false;
  $("#retake-confirm-row").classList.add("hidden");
  $("#selection-phase").value = "confirmation";
  $$('input[name="input-mode"]').forEach((input) => { input.checked = input.value === "manual"; });
  $$(".mode-card").forEach((card) => card.classList.toggle("active", card.querySelector('input[value="manual"]') !== null));
  updateInputModeUi();
  renderSelectedCourses();
  renderRules();
  $("#plan-results").innerHTML = '<div class="empty-state result-empty"><span>▦</span><p>完成前三步后，在这里查看候选课表</p></div>';
  $("#draft-save-status").textContent = "修改课程或约束后自动保存";
  $("#recent-plans").innerHTML = '<p class="muted">暂无历史方案。</p>';
  $("#result-stale-alert").classList.add("hidden");
  $("#result-stale-alert").textContent = "";
  setProfileComplete(false);
}

async function loadProfile() {
  try {
    state.profile = await api("/api/profile");
    fillProfileForm();
    setProfileComplete(true);
  } catch (error) {
    if (error.message.includes("尚未填写")) {
      state.profile = null;
      $("#profile-form").reset();
      setProfileComplete(false);
    }
    else throw error;
  }
}

function fillProfileForm() {
  const profile = state.profile;
  if (!profile) return;
  $("#profile-college").value = profile.college;
  $("#profile-major").value = profile.major;
  $("#profile-cohort").value = String(profile.cohort_year);
  $("#profile-variant").value = profile.plan_variant || "";
  $("#profile-major-code").value = profile.major_code || "";
  $("#profile-administrative-class").value = profile.administrative_class || "";
  $("#profile-cooperation").value = profile.cooperation_program || "无";
  $("#profile-semester").value = profile.semester_override ? String(profile.semester_override) : "";
  updateSemesterNote();
}

function setProfileComplete(complete) {
  $("#profile-status").textContent = complete ? "已保存" : "待填写";
  $("#profile-status").className = `badge ${complete ? "success" : "warning"}`;
  $$(".locked-section").forEach((section) => {
    section.classList.toggle("is-locked", !complete);
    section.setAttribute("aria-disabled", String(!complete));
    [...section.children]
      .filter((child) => !child.classList.contains("section-heading") && !child.classList.contains("lock-note"))
      .forEach((child) => { child.inert = !complete; });
  });
  if (complete) matchCurriculum();
}

async function saveProfile(event) {
  event.preventDefault();
  $("#profile-error").textContent = "";
  const payload = {
    college: $("#profile-college").value,
    major: $("#profile-major").value,
    cohort_year: Number($("#profile-cohort").value),
    plan_variant: $("#profile-variant").value || null,
    major_code: $("#profile-major-code").value || null,
    administrative_class: $("#profile-administrative-class").value.trim() || null,
    cooperation_program: $("#profile-cooperation").value,
    semester_override: $("#profile-semester").value ? Number($("#profile-semester").value) : null,
  };
  try {
    state.profile = await api("/api/profile", { method: "PUT", body: JSON.stringify(payload) });
    setProfileComplete(true);
    updateSemesterNote();
    toast("学生信息已保存");
  } catch (error) { $("#profile-error").textContent = error.message; }
}

function updateSemesterNote() {
  const cohort = Number($("#profile-cohort").value);
  if (!cohort) return;
  const inferred = 2 * (2026 - cohort) + 1;
  const override = Number($("#profile-semester").value) || inferred;
  $("#semester-note").textContent = `2026 秋季按年级推算为第 ${inferred} 学期；当前采用第 ${override} 学期。转专业、休学或留级时请修正。`;
}

async function loadCatalogStatus() {
  try {
    state.catalog = await api("/api/catalog/status");
    const node = $("#catalog-mini-status");
    const dot = node.querySelector(".status-dot");
    dot.className = `status-dot ${state.catalog.ready ? "" : "error"}`;
    node.querySelector("strong").textContent = state.catalog.ready ? `${state.catalog.course_count} 门课程` : "课程库未就绪";
    node.querySelector("small").textContent = state.catalog.ready
      ? `${state.catalog.primary_section_count} 个可靠候选；${state.catalog.confirmation_required_count} 个需确认`
      : state.catalog.warning;
  } catch (error) { toast(error.message, true); }
}

function setInputMode(event) {
  state.inputMode = event.target.value;
  $$(".mode-card").forEach((card) => card.classList.toggle("active", card.contains(event.target)));
  updateInputModeUi();
  if (state.inputMode !== "manual") matchCurriculum();
  else if (state.curriculumPreview) renderCurriculumCourseList();
  scheduleDraftSave();
}

function updateInputModeUi() {
  const isManual = state.inputMode === "manual";
  const isPureCurriculum = state.inputMode === "curriculum";
  $("#curriculum-panel").classList.toggle("hidden", isManual);
  $("#optional-courses-row").classList.toggle("hidden", state.inputMode !== "mixed");
  $("#pure-curriculum-note").classList.toggle("hidden", !isPureCurriculum);
  $("#import-curriculum").classList.toggle("hidden", isPureCurriculum);
  $("#manual-course-workspace").classList.toggle("hidden", isPureCurriculum);
  if (isPureCurriculum) {
    const safeCount = (state.curriculumPreview?.courses || []).filter(isSafeRequiredCurriculumCourse).length;
    $("#course-count").textContent = `${safeCount} 门自动候选`;
  } else {
    $("#course-count").textContent = `${state.selectedCourses.size} 门`;
  }
}

async function matchCurriculum() {
  if (!state.profile) return;
  const params = new URLSearchParams({
    college: state.profile.college,
    major: state.profile.major,
    cohort_year: state.profile.cohort_year,
    semester: state.profile.semester,
  });
  if (state.profile.plan_variant) params.set("plan_variant", state.profile.plan_variant);
  try {
    state.curriculumPreview = await api(`/api/curricula/preview?${params}`);
    renderCurriculumEvidence();
  } catch (error) {
    state.curriculumPreview = null;
    $("#curriculum-status").textContent = "无法读取";
    $("#curriculum-status").className = "badge danger";
    $("#curriculum-evidence").textContent = error.message;
    $("#import-curriculum").disabled = true;
    $("#curriculum-course-list").innerHTML = "";
  }
}

function renderCurriculumEvidence() {
  const preview = state.curriculumPreview;
  if (!preview || preview.manual_only) {
    $("#curriculum-status").textContent = "仅支持手动输入";
    $("#curriculum-status").className = "badge warning";
    $("#curriculum-evidence").innerHTML = `<strong>尚未找到可直接导入的最新官方培养方案。</strong><br>${escapeHtml(preview?.warnings?.join("；") || "请使用手动输入方式。")}`;
    $("#import-curriculum").disabled = true;
    $("#curriculum-course-list").innerHTML = "";
    state.inputMode = "manual";
    $$('input[name="input-mode"]').forEach((input) => {
      input.disabled = input.value !== "manual";
      input.checked = input.value === "manual";
    });
    $$(".mode-card").forEach((card) => {
      card.classList.toggle("active", card.querySelector('input[value="manual"]') !== null);
    });
    updateInputModeUi();
    $("#curriculum-panel").classList.remove("hidden");
    $("#optional-courses-row").classList.add("hidden");
    $("#import-curriculum").classList.add("hidden");
    return;
  }
  $$('input[name="input-mode"]').forEach((input) => { input.disabled = false; });
  const source = preview.source;
  const hasDirectionChoice = preview.courses.some((course) => course.requirement_type === "track_choice");
  const safeCount = preview.courses.filter(isSafeRequiredCurriculumCourse).length;
  const pureHasOmissions = state.inputMode === "curriculum" && preview.courses.some((course) => !isSafeRequiredCurriculumCourse(course));
  $("#curriculum-status").textContent = state.inputMode === "curriculum"
    ? `${safeCount} 门安全必修`
    : `${preview.courses.length} 门可核对`;
  $("#curriculum-status").className = `badge ${pureHasOmissions ? "warning" : "success"}`;
  const choiceNotice = hasDirectionChoice
    ? "<br><strong>包含方向课程组；纯模式不会代替你选择，混合方式可自行选定。</strong>"
    : "";
  $("#curriculum-evidence").innerHTML = `<strong>${escapeHtml(source.major)} · 第 ${preview.semester} 学期</strong><br>状态：${escapeHtml(source.status)}；官网核验日期：${escapeHtml(source.checked_at || "未记录")}。${source.official_url ? `<a href="${escapeAttribute(source.official_url)}" target="_blank" rel="noreferrer">查看官方来源</a>` : ""}<br>${preview.warnings.map(escapeHtml).join("；")}${choiceNotice}`;
  $("#import-curriculum").disabled = preview.courses.length === 0;
  updateInputModeUi();
  renderCurriculumCourseList();
}

function isSafeRequiredCurriculumCourse(course) {
  return Boolean(course.required && course.matched_course_id && Number(course.eligible_section_count || 0) > 0);
}

async function importCurriculumCourses() {
  if (state.inputMode !== "mixed") return;
  if (!$("#confirm-semester").checked) return toast("请先确认实际所在学期", true);
  const checkedIndexes = new Set(
    $$("[data-curriculum-index]:checked").map((input) => Number(input.dataset.curriculumIndex))
  );
  const courses = (state.curriculumPreview?.courses || []).filter((_, index) => checkedIndexes.has(index));
  const groups = new Set(
    (state.curriculumPreview?.courses || [])
      .filter((course) => course.requirement_type === "track_choice")
      .map((course) => course.selection_group || "未命名方向组")
  );
  for (const group of groups) {
    const chosen = courses.some(
      (course) => course.requirement_type === "track_choice"
        && (course.selection_group || "未命名方向组") === group
    );
    if (!chosen) return toast(`请选择方向课程组“${group}”中的一个方向`, true);
  }
  const importedIds = [];
  courses.forEach((course) => {
    if (!course.matched_course_id) return;
    const normalized = normalizeCourseSummary({
      id: course.matched_course_id, code: course.code, name: course.name,
      credits: course.credits, required: course.required, priority: course.required ? 200 : 80,
      allow_confirmation_required: false, allow_unknown_time: false, locked_section_id: null,
      forbidden_section_ids: [],
      section_count: course.section_count,
      confirmation_required_section_count: course.confirmation_required_section_count,
      legacy_only_section_count: course.legacy_only_section_count,
      data_quality_confirmation_section_count: course.data_quality_confirmation_section_count,
      unknown_time_section_count: course.unknown_time_section_count,
    });
    state.selectedCourses.set(course.matched_course_id, normalized);
    importedIds.push(course.matched_course_id);
  });
  renderSelectedCourses();
  scheduleDraftSave();
  toast(`已载入 ${importedIds.length} 门已匹配课程，正在读取教学班详情`);
  await Promise.allSettled(importedIds.map((courseId) => ensureCourseDetail(courseId)));
}

function renderCurriculumCourseList() {
  const container = $("#curriculum-course-list");
  const courses = state.curriculumPreview?.courses || [];
  if (!courses.length) {
    container.innerHTML = "";
    return;
  }
  const isPureCurriculum = state.inputMode === "curriculum";
  const groupRules = new Map();
  courses.forEach((course) => {
    if (course.requirement_type === "track_choice") {
      groupRules.set(
        course.selection_group || "未命名方向组",
        course.selection_rule || "该方向组需要做出选择"
      );
    }
  });
  const rows = [...groupRules].map(
    ([group, rule]) => `<div class="curriculum-group-note"><strong>${escapeHtml(group)}</strong>：${escapeHtml(rule)}</div>`
  );
  courses.forEach((course, index) => {
    const isRequired = course.required;
    const isChoice = course.requirement_type === "track_choice";
    const inputType = isChoice ? "radio" : "checkbox";
    const inputName = isChoice
      ? `curriculum-group-${course.selection_group || "unnamed"}`
      : `curriculum-${index}`;
    const checked = isRequired ? "checked" : "";
    const disabled = isPureCurriculum || !course.matched_course_id
      ? "disabled" : "";
    const tag = isRequired ? "必修" : isChoice ? "方向选择" : "选修候选";
    const tagClass = isRequired ? "required" : isChoice ? "choice" : "";
    if (isPureCurriculum) {
      let status = "不会自动提交";
      let statusClass = "choice";
      if (isSafeRequiredCurriculumCourse(course)) {
        status = "将自动提交";
        statusClass = "required";
      } else if (course.required && !course.matched_course_id) {
        status = "未匹配 · 结果持续提示";
      } else if (course.required && Number(course.eligible_section_count || 0) === 0) {
        status = "仅有风险班 · 结果持续提示";
      } else if (isChoice) {
        status = "方向待选 · 改用混合方式";
      }
      rows.push(`<div class="curriculum-course-row curriculum-course-readonly"><span class="course-code">${escapeHtml(course.code)}</span><strong title="${escapeAttribute(course.name)}">${escapeHtml(course.name)}</strong><span class="requirement-tag ${statusClass}">${escapeHtml(status)}</span></div>`);
      return;
    }
    rows.push(`<label class="curriculum-course-row"><input type="${inputType}" name="${escapeAttribute(inputName)}" data-curriculum-index="${index}" ${checked} ${disabled}/><span class="course-code">${escapeHtml(course.code)}</span><strong title="${escapeAttribute(course.name)}">${escapeHtml(course.name)}</strong><span class="requirement-tag ${tagClass}">${tag}${course.matched_course_id ? "" : " · 未匹配"}</span></label>`);
  });
  container.innerHTML = rows.join("");
}

function toggleOptionalCurriculumCourses() {
  if (state.inputMode !== "mixed") return;
  const enabled = $("#include-optional").checked;
  $$("#curriculum-course-list input[type='checkbox']").forEach((input) => {
    const course = state.curriculumPreview?.courses?.[Number(input.dataset.curriculumIndex)];
    if (course && !course.required && !input.disabled) input.checked = enabled;
  });
}

async function searchCourses() {
  const query = $("#course-search").value.trim();
  if (!query) return toast("请输入课程名称或课程号", true);
  const container = $("#course-search-results");
  container.innerHTML = '<div class="empty-state compact"><span class="spinner"></span><p>搜索中…</p></div>';
  try {
    const results = await api(`/api/catalog/search?q=${encodeURIComponent(query)}`);
    if (!results.length) {
      container.innerHTML = '<div class="empty-state compact"><span>∅</span><p>没有匹配课程</p><small>请检查课程号，或尝试较短的课程名称。</small></div>';
      return;
    }
    const normalizedResults = results.map(normalizeCourseSummary);
    container.innerHTML = normalizedResults.map((course) => `<div class="search-result">
      <span class="course-code">${escapeHtml(course.code)}</span>
      <div class="course-copy"><strong>${escapeHtml(course.name)}</strong><small>${escapeHtml(formatCourseRiskCounts(course))}</small></div>
      <button class="icon-button" data-add-course="${escapeAttribute(course.id)}" aria-label="添加 ${escapeAttribute(course.name)}">＋</button>
    </div>`).join("");
    container._courseResults = normalizedResults;
  } catch (error) { container.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`; }
}

function handleDelegatedClick(event) {
  const replaceDraftButton = event.target.closest("[data-replace-draft]");
  if (replaceDraftButton) {
    state.draftWriteBlocked = false;
    void savePlanningDraft({ force: true });
    return;
  }
  const historyButton = event.target.closest("[data-history-run-id]");
  if (historyButton) {
    void openHistoryRun(historyButton.dataset.historyRunId);
    return;
  }
  const addButton = event.target.closest("[data-add-course]");
  if (addButton) {
    const course = $("#course-search-results")._courseResults?.find((item) => item.id === addButton.dataset.addCourse);
    if (course) addCourse(course);
    return;
  }
  const retryDetailButton = event.target.closest("[data-retry-course-detail]");
  if (retryDetailButton) {
    const course = state.selectedCourses.get(retryDetailButton.dataset.retryCourseDetail);
    if (course) {
      delete course.detail_error;
      renderSelectedCourses();
      void ensureCourseDetail(course.id, true);
    }
    return;
  }
  const lockButton = event.target.closest("[data-lock-section]");
  if (lockButton) {
    const course = state.selectedCourses.get(lockButton.dataset.sectionCourseId);
    if (!course) return;
    const sectionId = lockButton.dataset.lockSection;
    const wasLocked = course.locked_section_id === sectionId;
    course.locked_section_id = wasLocked ? null : sectionId;
    if (!wasLocked) {
      course.forbidden_section_ids = (course.forbidden_section_ids || []).filter((id) => id !== sectionId);
    }
    renderSelectedCourses();
    scheduleDraftSave();
    toast(wasLocked ? "已取消教学班锁定" : "已锁定该教学班；自动排课不会改选其他班");
    return;
  }
  const forbidButton = event.target.closest("[data-forbid-section]");
  if (forbidButton) {
    const course = state.selectedCourses.get(forbidButton.dataset.sectionCourseId);
    if (!course) return;
    const sectionId = forbidButton.dataset.forbidSection;
    const forbidden = new Set(course.forbidden_section_ids || []);
    if (forbidden.has(sectionId)) forbidden.delete(sectionId);
    else {
      forbidden.add(sectionId);
      if (course.locked_section_id === sectionId) course.locked_section_id = null;
    }
    course.forbidden_section_ids = [...forbidden];
    renderSelectedCourses();
    scheduleDraftSave();
    toast(forbidden.has(sectionId) ? "已排除该教学班" : "已取消排除该教学班");
    return;
  }
  const removeButton = event.target.closest("[data-remove-course]");
  if (removeButton) {
    state.selectedCourses.delete(removeButton.dataset.removeCourse);
    renderSelectedCourses();
    scheduleDraftSave();
    return;
  }
  const removeRule = event.target.closest("[data-remove-rule]");
  if (removeRule) {
    const [type, id] = removeRule.dataset.removeRule.split(":");
    if (type === "time") state.blockedTimes = state.blockedTimes.filter((item) => item.id !== id);
    else state.instructorRules = state.instructorRules.filter((item) => item.id !== id);
    renderRules();
    scheduleDraftSave();
  }
  const planTab = event.target.closest("[data-plan-index]");
  if (planTab) {
    const index = Number(planTab.dataset.planIndex);
    renderPlanAt(index);
    requestAnimationFrame(() => $(`[data-plan-index="${index}"]`)?.focus());
  }
}

function handleDelegatedKeydown(event) {
  const authTab = event.target.closest("[data-auth-mode]");
  if (authTab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    const tabs = $$('[data-auth-mode]');
    const currentIndex = tabs.indexOf(authTab);
    const nextIndex = event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
      : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    setAuthMode(tabs[nextIndex].dataset.authMode);
    tabs[nextIndex].focus();
    return;
  }
  const planTab = event.target.closest("[data-plan-index]");
  if (!planTab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const planCount = state.planResponse?.plans?.length || 0;
  if (!planCount) return;
  const currentIndex = Number(planTab.dataset.planIndex);
  const nextIndex = event.key === "Home" ? 0
    : event.key === "End" ? planCount - 1
    : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + planCount) % planCount;
  event.preventDefault();
  renderPlanAt(nextIndex);
  requestAnimationFrame(() => $(`[data-plan-index="${nextIndex}"]`)?.focus());
}

function handleDelegatedChange(event) {
  const card = event.target.closest("[data-course-id]");
  const course = card ? state.selectedCourses.get(card.dataset.courseId) : null;
  let changed = false;
  if (course && event.target.matches("[data-course-field]")) {
    const field = event.target.dataset.courseField;
    course[field] = event.target.type === "checkbox" ? event.target.checked : field === "priority" ? Number(event.target.value) : event.target.value || null;
    changed = true;
  }
  if (event.target.matches("#prefer-no-early, #prefer-no-evening, #prefer-compact, #selection-phase, #retake-confirm, #confirm-semester, #include-optional")) changed = true;
  if (changed) scheduleDraftSave();
}

function addCourse(course) {
  if (state.selectedCourses.has(course.id)) return toast("这门课程已经在待排列表中");
  state.selectedCourses.set(course.id, normalizeCourseSummary({
    ...course,
    required: false,
    priority: 100,
    allow_confirmation_required: false,
    allow_unknown_time: false,
    locked_section_id: null,
    forbidden_section_ids: [],
  }));
  renderSelectedCourses();
  scheduleDraftSave();
  toast(`已添加：${course.name}；正在读取教学班详情`);
  void ensureCourseDetail(course.id);
}

function renderSelectedCourses() {
  const container = $("#selected-courses");
  const openCourseIds = new Set(
    [...container.querySelectorAll(".selected-course")]
      .filter((card) => card.querySelector(".section-picker")?.open)
      .map((card) => card.dataset.courseId)
  );
  const listScrollPositions = new Map(
    [...container.querySelectorAll(".selected-course")].map((card) => [
      card.dataset.courseId,
      card.querySelector(".section-option-list")?.scrollTop || 0,
    ])
  );
  const activeElement = document.activeElement;
  let focusState = null;
  if (activeElement && container.contains(activeElement)) {
    const courseId = activeElement.dataset.sectionCourseId;
    if (activeElement.matches("[data-lock-section]")) focusState = { type: "lock", courseId, sectionId: activeElement.dataset.lockSection };
    else if (activeElement.matches("[data-forbid-section]")) focusState = { type: "forbid", courseId, sectionId: activeElement.dataset.forbidSection };
  }
  const courses = [...state.selectedCourses.values()];
  if (state.inputMode !== "curriculum") $("#course-count").textContent = `${courses.length} 门`;
  if (!courses.length) {
    container.innerHTML = '<div class="empty-state"><span>＋</span><p>还没有添加课程</p><small>排课时每门课最多选择一个教学班组合。</small></div>';
    return;
  }
  container.innerHTML = courses.map((course) => {
    const legacyCount = Number(course.legacy_only_section_count || 0);
    const dataQualityCount = Number(course.data_quality_confirmation_section_count || 0);
    const confirmationCount = legacyCount + dataQualityCount;
    return `<div class="selected-course" data-course-id="${escapeAttribute(course.id)}">
    <div class="selected-course-header"><span class="course-code">${escapeHtml(course.code)}</span><div class="course-copy"><strong>${escapeHtml(course.name)}</strong><small>${course.section_count ?? "培养方案"} 个教学班</small></div><button class="icon-button" data-remove-course="${escapeAttribute(course.id)}" aria-label="移除 ${escapeAttribute(course.name)}">×</button></div>
    <div class="selected-course-controls">
      <label>重要程度<select data-course-field="priority"><option value="50" ${course.priority === 50 ? "selected" : ""}>可选</option><option value="100" ${course.priority === 100 ? "selected" : ""}>普通</option><option value="200" ${course.priority === 200 ? "selected" : ""}>重要</option></select></label>
      <label class="toggle-row"><input type="checkbox" data-course-field="required" ${course.required ? "checked" : ""}/><span><strong>必须排入</strong></span></label>
    </div>
    ${confirmationCount ? `<label class="quality-warning"><input type="checkbox" data-course-field="allow_confirmation_required" ${course.allow_confirmation_required ? "checked" : ""}/>允许使用需额外确认的教学班（旧版独有 ${legacyCount} 个；数据质量需确认 ${dataQualityCount} 个）；生成后仍须到教务系统核验</label>` : ""}
    ${course.unknown_time_section_count ? `<label class="quality-warning"><input type="checkbox" data-course-field="allow_unknown_time" ${course.allow_unknown_time ? "checked" : ""}/>允许采用仅有周次、具体时段待定的教学班；方案会持续显示风险</label>` : ""}
    ${renderSectionPicker(course)}
  </div>`;
  }).join("");
  [...container.querySelectorAll(".selected-course")].forEach((card) => {
    const details = card.querySelector(".section-picker");
    if (details && openCourseIds.has(card.dataset.courseId)) details.open = true;
    const list = card.querySelector(".section-option-list");
    if (list) list.scrollTop = listScrollPositions.get(card.dataset.courseId) || 0;
  });
  if (focusState) {
    const selector = focusState.type === "lock" ? "[data-lock-section]" : "[data-forbid-section]";
    const target = [...container.querySelectorAll(selector)].find((button) =>
      button.dataset.sectionCourseId === focusState.courseId
      && (focusState.type === "lock" ? button.dataset.lockSection : button.dataset.forbidSection) === focusState.sectionId
    );
    target?.focus({ preventScroll: true });
  }
}

function normalizeCourseSummary(course) {
  const legacyCount = Number(course.legacy_only_section_count || 0);
  const totalConfirmationCount = Number(course.confirmation_required_section_count || 0);
  const dataQualityCount = course.data_quality_confirmation_section_count == null
    ? Math.max(0, totalConfirmationCount - legacyCount)
    : Number(course.data_quality_confirmation_section_count || 0);
  return {
    ...course,
    confirmation_required_section_count: totalConfirmationCount,
    legacy_only_section_count: legacyCount,
    data_quality_confirmation_section_count: dataQualityCount,
    unknown_time_section_count: Number(course.unknown_time_section_count || 0),
    forbidden_section_ids: [...new Set(course.forbidden_section_ids || [])],
  };
}

function formatCourseRiskCounts(course) {
  const labels = [`${course.section_count} 个教学班`];
  if (course.legacy_only_section_count) labels.push(`旧版独有 ${course.legacy_only_section_count}`);
  if (course.data_quality_confirmation_section_count) labels.push(`数据质量需确认 ${course.data_quality_confirmation_section_count}`);
  if (course.unknown_time_section_count) labels.push(`时段不精确 ${course.unknown_time_section_count}`);
  return labels.join(" · ");
}

async function ensureCourseDetail(courseId, force = false) {
  if (!force && state.courseDetails.has(courseId)) return state.courseDetails.get(courseId);
  if (state.courseDetailRequests.has(courseId)) return state.courseDetailRequests.get(courseId);
  const request = api(`/api/catalog/courses/${encodeURIComponent(courseId)}`)
    .then((detail) => {
      state.courseDetails.set(courseId, detail);
      const course = state.selectedCourses.get(courseId);
      if (course) {
        const preferences = {
          required: course.required,
          priority: course.priority,
          allow_confirmation_required: course.allow_confirmation_required,
          allow_unknown_time: course.allow_unknown_time,
          locked_section_id: course.locked_section_id,
          forbidden_section_ids: course.forbidden_section_ids || [],
        };
        Object.assign(course, normalizeCourseSummary(detail), preferences);
        const sectionIds = new Set((detail.sections || []).map((section) => section.id));
        course.forbidden_section_ids = course.forbidden_section_ids.filter((id) => sectionIds.has(id));
        if (course.locked_section_id && !sectionIds.has(course.locked_section_id)) course.locked_section_id = null;
        delete course.detail_error;
        renderSelectedCourses();
      }
      return detail;
    })
    .catch((error) => {
      const course = state.selectedCourses.get(courseId);
      if (course) {
        course.detail_error = error.message;
        renderSelectedCourses();
      }
      return null;
    })
    .finally(() => state.courseDetailRequests.delete(courseId));
  state.courseDetailRequests.set(courseId, request);
  return request;
}

function renderSectionPicker(course) {
  const detail = state.courseDetails.get(course.id);
  if (course.detail_error) {
    return `<div class="section-detail-state danger-text">教学班详情读取失败：${escapeHtml(course.detail_error)} <button class="text-button" type="button" data-retry-course-detail="${escapeAttribute(course.id)}">重试</button></div>`;
  }
  if (!detail) return '<div class="section-detail-state"><span class="spinner small-spinner"></span> 正在加载教学班详情…</div>';
  const sections = [...(detail.sections || [])].sort((left, right) =>
    Number(right.default_eligible) - Number(left.default_eligible)
    || String(left.section_code).localeCompare(String(right.section_code), "zh-CN")
  );
  if (!sections.length) return '<div class="section-detail-state danger-text">课程库中没有可显示的教学班。</div>';
  const lockedCount = course.locked_section_id ? 1 : 0;
  const forbiddenCount = (course.forbidden_section_ids || []).length;
  return `<details class="section-picker"><summary>查看并指定教学班（${sections.length}）<span>${lockedCount ? "已锁定 1 个" : "自动选择"}${forbiddenCount ? ` · 已排除 ${forbiddenCount} 个` : ""}</span></summary><div class="section-option-list">${sections.map((section) => renderSectionOption(course, section)).join("")}</div></details>`;
}

function renderSectionOption(course, section) {
  const forbidden = (course.forbidden_section_ids || []).includes(section.id);
  const locked = course.locked_section_id === section.id;
  const issueCodes = new Set((section.issues || []).map((issue) => issue.code));
  const legacyOnly = issueCodes.has("old_snapshot_only");
  const unknownTime = (section.meetings || []).some((meeting) => ["week_only", "date_range", "tbd"].includes(meeting.precision));
  const dataQuality = Boolean(section.needs_confirmation && !legacyOnly && !unknownTime);
  const riskBadges = [
    legacyOnly ? '<span class="section-risk legacy">旧版独有 · 需确认</span>' : "",
    dataQuality ? '<span class="section-risk quality">数据质量需确认</span>' : "",
    unknownTime ? '<span class="section-risk time">时段不精确</span>' : "",
    section.capacity == null ? '<span class="section-risk neutral">容量未知</span>' : "",
  ].join("");
  const composition = (section.composition || []).filter(Boolean);
  const crossesClass = isCrossAdministrativeClass(composition);
  const classWarning = crossesClass
    ? `<div class="cross-class-warning">跨行政班候选：面向 ${escapeHtml(composition.join("、"))}，未列出你的行政班 ${escapeHtml(state.profile?.administrative_class)}。</div>`
    : "";
  const meetingRows = (section.meetings || []).length
    ? section.meetings.map((meeting) => `<li>${escapeHtml(formatSectionMeeting(meeting))}</li>`).join("")
    : "<li>时间与地点待确认</li>";
  return `<article class="section-option${locked ? " is-locked" : ""}${forbidden ? " is-forbidden" : ""}">
    <div class="section-option-heading"><div><strong>${escapeHtml(section.section_code || section.display_name || "未编号教学班")}</strong><small>${escapeHtml(section.display_name || "")}</small></div><div class="section-risks">${riskBadges}</div></div>
    <p><strong>教师：</strong>${escapeHtml((section.instructors || []).join("、") || "待定")}</p>
    <ul class="section-meetings">${meetingRows}</ul>
    <p><strong>教学班组成：</strong>${escapeHtml(composition.join("、") || "未标注")}</p>
    <p><strong>考核：</strong>${escapeHtml(section.assessment || "待定")} · <strong>已选人数快照：</strong>${section.enrolled_count ?? "未知"}（不是容量） · <strong>容量：</strong>${section.capacity ?? "未知"}</p>
    ${classWarning}
    <div class="section-actions">
      <button type="button" class="button secondary" data-lock-section="${escapeAttribute(section.id)}" data-section-course-id="${escapeAttribute(course.id)}" aria-pressed="${locked}">${locked ? "取消锁定" : "锁定此班"}</button>
      <button type="button" class="button secondary danger-outline" data-forbid-section="${escapeAttribute(section.id)}" data-section-course-id="${escapeAttribute(course.id)}" aria-pressed="${forbidden}">${forbidden ? "取消排除" : "排除此班"}</button>
    </div>
  </article>`;
}

function formatSectionMeeting(meeting) {
  const location = formatLocation(meeting.campus, meeting.room);
  const weeks = formatWeeks(meeting.weeks);
  if (meeting.precision === "async") return `异步或线上教学 · ${weeks}${location === "地点待定" ? "" : ` · ${location}`}`;
  if (meeting.precision === "exact_slot" && meeting.weekday && meeting.start_period && meeting.end_period) {
    return `${weekdayNames[meeting.weekday]} 第 ${meeting.start_period}–${meeting.end_period} 节 · ${weeks} · ${location}`;
  }
  const precisionLabels = { week_only: "星期与节次待确认", date_range: "日期范围记录，具体节次待确认", tbd: "时间待确认" };
  return `${weeks} · ${precisionLabels[meeting.precision] || "时段待确认"} · ${location}`;
}

function formatLocation(campus, room) {
  return [campus, room].filter(Boolean).join(" · ") || "地点待定";
}

function normalizeAdministrativeClass(value) {
  return String(value || "").replace(/[^\p{L}\p{N}]/gu, "").toLocaleLowerCase();
}

function isCrossAdministrativeClass(composition) {
  const expected = normalizeAdministrativeClass(state.profile?.administrative_class);
  if (!expected || !composition?.length) return false;
  return !composition.some((value) => normalizeAdministrativeClass(value).includes(expected));
}

function parseWeeks(value) {
  const weeks = new Set();
  value.replace(/，/g, ",").split(",").map((item) => item.trim()).filter(Boolean).forEach((part) => {
    const match = part.match(/^(\d+)(?:-(\d+))?$/);
    if (!match) throw new Error(`无法识别教学周“${part}”`);
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (start < 1 || end > 21 || end < start) throw new Error("教学周必须在 1–21 周内");
    for (let week = start; week <= end; week += 1) weeks.add(week);
  });
  if (!weeks.size) throw new Error("至少填写一个教学周");
  return [...weeks].sort((a, b) => a - b);
}

function addBlockedTime(event) {
  event.preventDefault();
  try {
    const start = Number($("#blocked-start").value), end = Number($("#blocked-end").value);
    if (end < start) throw new Error("结束节次不能早于开始节次");
    state.blockedTimes.push({
      id: `time-${crypto.randomUUID()}`, weekday: Number($("#blocked-weekday").value),
      start_period: start, end_period: end, weeks: parseWeeks($("#blocked-weeks").value),
      strength: $("#blocked-strength").value, penalty: 100, label: $("#blocked-label").value || null,
    });
    renderRules();
    scheduleDraftSave();
  } catch (error) { toast(error.message, true); }
}

function addTeacherRule(event) {
  event.preventDefault();
  const name = $("#teacher-name").value.trim();
  if (!name) return;
  state.instructorRules.push({ id: `teacher-${crypto.randomUUID()}`, instructor: name, strength: $("#teacher-strength").value, penalty: 100, label: $("#teacher-label").value || null });
  $("#teacher-name").value = "";
  renderRules();
  scheduleDraftSave();
}

function renderRules() {
  const allRules = [
    ...state.blockedTimes.map((rule) => ({ id: rule.id, type: "time", tag: rule.strength === "hard" ? "硬约束" : "软偏好", text: `${weekdayNames[rule.weekday]} 第 ${rule.start_period}–${rule.end_period} 节 · ${formatWeeks(rule.weeks)}${rule.label ? ` · ${rule.label}` : ""}` })),
    ...state.instructorRules.map((rule) => ({ id: rule.id, type: "teacher", tag: rule.strength === "hard" ? "不选教师" : "尽量避开", text: `${rule.instructor}${rule.label ? ` · ${rule.label}` : ""}` })),
  ];
  $("#constraint-count").textContent = `${allRules.length} 条`;
  $("#constraint-list").innerHTML = allRules.map((rule) => `<div class="rule-chip"><span>${escapeHtml(rule.tag)}</span><strong>${escapeHtml(rule.text)}</strong><button class="icon-button" data-remove-rule="${rule.type}:${escapeAttribute(rule.id)}" aria-label="删除规则：${escapeAttribute(rule.text)}">×</button></div>`).join("");
}

function buildPlanningPayload({ forDraft = false } = {}) {
  const includeManualCourses = forDraft || state.inputMode !== "curriculum";
  const courses = includeManualCourses ? [...state.selectedCourses.values()] : [];
  const forbiddenSectionIds = [...new Set(courses.flatMap((course) =>
    (course.forbidden_section_ids || []).filter((sectionId) => sectionId !== course.locked_section_id)
  ))];
  return {
    input_mode: state.inputMode,
    manual_courses: courses.map((course) => ({
      course_id: course.id,
      priority: course.priority,
      required: course.required,
      locked_section_id: course.locked_section_id || null,
      allow_confirmation_required: Boolean(course.allow_confirmation_required),
      allow_unknown_time: Boolean(course.allow_unknown_time),
    })),
    curriculum: state.inputMode === "manual" ? null : {
      source_id: state.curriculumPreview?.source?.id || null,
      semester: state.profile?.semester || null,
      include_optional: state.inputMode === "mixed" && $("#include-optional").checked,
      confirmed_by_user: $("#confirm-semester").checked,
    },
    preferences: {
      blocked_times: state.blockedTimes,
      instructor_rules: state.instructorRules,
      prefer_no_early_class: $("#prefer-no-early").checked,
      prefer_no_evening_class: $("#prefer-no-evening").checked,
      prefer_compact_days: $("#prefer-compact").checked,
      phase: $("#selection-phase").value,
      max_solutions: PLANNING_RESULT_LIMIT,
      forbidden_section_ids: forbiddenSectionIds,
      retake_eligibility_confirmed: $("#retake-confirm").checked,
    },
  };
}

function scheduleDraftSave() {
  if (!state.token || state.draftRestoreInProgress || state.draftWriteBlocked) return;
  clearTimeout(state.draftSaveTimer);
  $("#draft-save-status").textContent = "有未保存的修改…";
  state.draftSaveTimer = setTimeout(() => { void savePlanningDraft(); }, 750);
}

async function savePlanningDraft({ silent = false, force = false } = {}) {
  if (!state.token || state.draftRestoreInProgress || (state.draftWriteBlocked && !force)) return null;
  clearTimeout(state.draftSaveTimer);
  state.draftSaveTimer = null;
  $("#draft-save-status").textContent = "正在保存草稿…";
  try {
    const response = await api("/api/plans/draft", {
      method: "PUT",
      body: JSON.stringify(buildPlanningPayload({ forDraft: true })),
    });
    const savedAt = new Date(response.updated_at);
    $("#draft-save-status").textContent = `草稿已自动保存 · ${Number.isNaN(savedAt.getTime()) ? "刚刚" : savedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
    return response;
  } catch (error) {
    $("#draft-save-status").textContent = `草稿保存失败：${error.message}`;
    if (!silent) toast(`草稿保存失败：${error.message}`, true);
    return null;
  }
}

async function restorePlanningDraft() {
  if (!state.token) return;
  let response;
  try {
    response = await api("/api/plans/draft");
  } catch (error) {
    if (error.status === 404) {
      $("#draft-save-status").textContent = "尚无草稿；修改后将自动保存";
      return;
    }
    $("#draft-save-status").textContent = `草稿无法恢复：${error.message}`;
    if (error.status === 409) {
      state.draftWriteBlocked = true;
      $("#draft-save-status").textContent = "草稿自动保存已暂停，避免覆盖无法读取的旧草稿";
      showStaleAlert(`草稿格式无法安全读取：${error.message}`, true);
    }
    return;
  }
  const draft = response.draft || {};
  const preferences = draft.preferences || {};
  state.draftRestoreInProgress = true;
  try {
    state.inputMode = draft.input_mode || "manual";
    $$('input[name="input-mode"]').forEach((input) => { input.checked = input.value === state.inputMode; });
    $$(".mode-card").forEach((card) => card.classList.toggle("active", Boolean(card.querySelector(`input[value="${state.inputMode}"]`))));
    state.blockedTimes = Array.isArray(preferences.blocked_times) ? preferences.blocked_times : [];
    state.instructorRules = Array.isArray(preferences.instructor_rules) ? preferences.instructor_rules : [];
    $("#prefer-no-early").checked = Boolean(preferences.prefer_no_early_class);
    $("#prefer-no-evening").checked = Boolean(preferences.prefer_no_evening_class);
    $("#prefer-compact").checked = Boolean(preferences.prefer_compact_days);
    $("#selection-phase").value = preferences.phase || "confirmation";
    $("#retake-confirm").checked = Boolean(preferences.retake_eligibility_confirmed);
    $("#confirm-semester").checked = Boolean(draft.curriculum?.confirmed_by_user);
    $("#include-optional").checked = Boolean(draft.curriculum?.include_optional);
    updateRetakeConfirmation();
    state.selectedCourses = new Map((draft.manual_courses || []).map((choice) => [choice.course_id, normalizeCourseSummary({
      id: choice.course_id,
      code: choice.course_id,
      name: "正在恢复课程详情…",
      section_count: 0,
      required: Boolean(choice.required),
      priority: Number(choice.priority ?? 100),
      allow_confirmation_required: Boolean(choice.allow_confirmation_required),
      allow_unknown_time: Boolean(choice.allow_unknown_time),
      locked_section_id: choice.locked_section_id || null,
      forbidden_section_ids: [],
    })]));
    updateInputModeUi();
    renderRules();
    renderSelectedCourses();
    if (state.inputMode !== "manual" && state.profile) await matchCurriculum();
    await Promise.all([...state.selectedCourses.keys()].map((courseId) => ensureCourseDetail(courseId)));
    const forbidden = new Set(preferences.forbidden_section_ids || []);
    state.selectedCourses.forEach((course) => {
      const detail = state.courseDetails.get(course.id);
      course.forbidden_section_ids = (detail?.sections || [])
        .map((section) => section.id)
        .filter((sectionId) => forbidden.has(sectionId) && sectionId !== course.locked_section_id);
    });
    renderSelectedCourses();
    const restoredAt = new Date(response.updated_at);
    $("#draft-save-status").textContent = `已恢复草稿 · ${Number.isNaN(restoredAt.getTime()) ? "时间未知" : restoredAt.toLocaleString("zh-CN")}`;
    if (response.catalog_is_stale) {
      showStaleAlert(response.stale_reason || "课程总库已更新；已恢复的草稿需要重新检查教学班并求解。");
    }
  } finally {
    state.draftRestoreInProgress = false;
  }
}

async function loadPlanningHistory() {
  if (!state.token) return;
  $("#recent-plans").innerHTML = '<p class="muted">正在读取最近方案…</p>';
  try {
    state.historyRuns = await api("/api/plans/history?limit=10");
    renderPlanningHistory();
  } catch (error) {
    $("#recent-plans").innerHTML = `<p class="form-error">历史方案读取失败：${escapeHtml(error.message)}</p>`;
  }
}

function renderPlanningHistory() {
  const container = $("#recent-plans");
  if (!state.historyRuns.length) {
    container.innerHTML = '<p class="muted">暂无历史方案；首次生成后会保存在这里。</p>';
    return;
  }
  const modeLabels = { manual: "手动", curriculum: "培养方案", mixed: "混合" };
  container.innerHTML = state.historyRuns.map((run) => {
    const createdAt = new Date(run.created_at);
    const time = Number.isNaN(createdAt.getTime()) ? "时间未知" : createdAt.toLocaleString("zh-CN");
    return `<button type="button" class="history-run${run.catalog_is_stale ? " is-stale" : ""}" data-history-run-id="${escapeAttribute(run.run_id)}">
      <span><strong>${escapeHtml(modeLabels[run.input_mode] || run.input_mode)} · ${run.scheduled_course_count} 门</strong><small>${escapeHtml(time)} · ${run.plan_count} 个方案</small></span>
      <span class="history-status">${run.catalog_is_stale ? "课程库已更新" : "打开"}</span>
    </button>`;
  }).join("");
}

async function openHistoryRun(runId) {
  $("#plan-results").innerHTML = '<div class="plan-progress"><span class="spinner"></span><div><strong>正在读取历史方案…</strong></div></div>';
  try {
    const detail = await api(`/api/plans/history/${encodeURIComponent(runId)}`);
    state.currentResultMeta = detail;
    state.planResponse = detail.result;
    await ensureResultCourseDetails(state.planResponse);
    showResultStaleness(detail);
    renderPlanResults();
    $("#result-step").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#plan-results").innerHTML = `<div class="diagnostic">历史方案读取失败：${escapeHtml(error.message)}</div>`;
  }
}

function showStaleAlert(message, allowDraftReplace = false) {
  const alert = $("#result-stale-alert");
  alert.innerHTML = `<strong>${allowDraftReplace ? "草稿需处理" : "需要重新求解"}</strong><span>${escapeHtml(message)}${allowDraftReplace ? '<br><button type="button" class="button secondary small-button" data-replace-draft>用当前页面状态覆盖旧草稿</button>' : ""}</span>`;
  alert.classList.remove("hidden");
}

function showResultStaleness(meta) {
  const alert = $("#result-stale-alert");
  if (meta?.schema_version != null && meta.schema_version !== 1) {
    showStaleAlert(`此历史方案使用格式版本 ${meta.schema_version}，当前界面只完整支持版本 1；请重新生成后再使用。`);
  } else if (meta?.catalog_is_stale) {
    showStaleAlert(meta.stale_reason || "课程总库已更新；此历史方案只能用于回看，请重新生成。");
  } else {
    alert.classList.add("hidden");
    alert.textContent = "";
  }
}

async function generatePlan() {
  if (!state.profile) return toast("请先保存学院、专业和年级", true);
  if (state.inputMode === "manual" && !state.selectedCourses.size) return toast("请至少添加一门课程", true);
  if (state.inputMode !== "manual" && (!state.curriculumPreview || state.curriculumPreview.manual_only)) return toast("当前身份没有可解析培养方案，只能使用手动输入", true);
  if (state.inputMode !== "manual" && !$("#confirm-semester").checked) return toast("请先确认实际所在学期", true);
  const payload = buildPlanningPayload();
  $("#plan-progress").classList.remove("hidden");
  $("#plan-results").innerHTML = "";
  $("#generate-plan").disabled = true;
  try {
    await savePlanningDraft({ silent: true });
    state.planResponse = await api("/api/plans/generate", { method: "POST", body: JSON.stringify(payload) });
    state.currentResultMeta = { catalog_is_stale: false };
    await ensureResultCourseDetails(state.planResponse);
    showResultStaleness(state.currentResultMeta);
    renderPlanResults();
    void loadPlanningHistory();
  } catch (error) {
    $("#plan-results").innerHTML = `<div class="diagnostic">${escapeHtml(error.message)}</div>`;
  } finally {
    $("#plan-progress").classList.add("hidden");
    $("#generate-plan").disabled = false;
  }
}

async function ensureResultCourseDetails(result) {
  if (state.currentResultMeta?.catalog_is_stale) return;
  const courseIds = [...new Set((result?.plans || []).flatMap((plan) =>
    (plan.selected_courses || []).map((course) => course.course_id)
  ))];
  await Promise.all(courseIds.map((courseId) => ensureCourseDetail(courseId)));
}

function renderPlanResults() {
  const result = state.planResponse;
  if (!result?.plans?.length) {
    const diagnostics = result?.diagnostics || [];
    const warnings = result?.warnings || [];
    const warningRows = warnings.map((message) => `<div class="diagnostic persistent-warning">${escapeHtml(message)}</div>`).join("");
    const diagnosticRows = diagnostics.map((item) => `<div class="diagnostic">${escapeHtml(item.message)}</div>`).join("");
    $("#plan-results").innerHTML = `<div class="card"><h3>没有找到可执行方案</h3><p class="muted">状态：${escapeHtml(result?.status || "未生成")}</p><div class="diagnostic-list">${warningRows}${diagnosticRows || '<div class="diagnostic">请检查必选课程、锁定教学班和硬约束。</div>'}</div></div>`;
    return;
  }
  renderPlanAt(0);
}

function formatPlanSetSummary(result) {
  const planCount = Array.isArray(result?.plans) ? result.plans.length : 0;
  const reportedLimit = Number(result?.plan_limit);
  const planLimit = Number.isInteger(reportedLimit) && reportedLimit > 0
    ? reportedLimit
    : PLANNING_RESULT_LIMIT;
  if (result?.plans_truncated === true) {
    if (result?.status !== "optimal") {
      return {
        kind: "unknown",
        text: `已找到 ${planCount} 种可行排课方式；求解受时限影响，未确认是否为全局前 ${planCount} 种`,
      };
    }
    return {
      kind: "truncated",
      text: `可行组合超过 ${planLimit} 种，已按推荐顺序列出前 ${Math.min(planCount, planLimit)} 种`,
    };
  }
  if (result?.all_plans_returned === true) {
    return { kind: "complete", text: `已列出全部 ${planCount} 种可行排课方式` };
  }
  const hasEnumerationMetadata = Object.prototype.hasOwnProperty.call(result || {}, "all_plans_returned")
    || Object.prototype.hasOwnProperty.call(result || {}, "plans_truncated");
  return {
    kind: "unknown",
    text: hasEnumerationMetadata
      ? result?.status === "optimal"
        ? `已按推荐顺序列出 ${planCount} 种可行排课方式；当前求解未能确认是否还有更多组合`
        : `已找到 ${planCount} 种可行排课方式；求解受时限影响，未确认是否还有更多组合`
      : `此历史结果包含 ${planCount} 种可行排课方式；旧记录未保存是否已经列完`,
  };
}

function renderPlanAt(index) {
  const result = state.planResponse, plan = result.plans[index];
  const tabs = result.plans.map((_, planIndex) => `<button type="button" role="tab" aria-selected="${planIndex === index}" class="plan-tab ${planIndex === index ? "active" : ""}" data-plan-index="${planIndex}">方案 ${planIndex + 1}</button>`).join("");
  const planSetSummary = formatPlanSetSummary(result);
  const meetings = plan.meetings || [];
  const selectedCourses = plan.selected_courses || [];
  const selectedByOption = new Map(selectedCourses.map((course) => [course.option_id, course]));
  const maximumPeriod = Math.min(20, Math.max(12, ...meetings.map((item) => item.end_period || 0)));
  const headers = ["节次", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    .map((text) => `<th scope="col">${text}</th>`).join("");
  const rows = [];
  for (let period = 1; period <= maximumPeriod; period += 1) {
    const cells = [`<th scope="row" class="period-cell">${period}</th>`];
    for (let weekday = 1; weekday <= 7; weekday += 1) {
      const items = meetings.filter((item) => item.weekday === weekday && item.start_period <= period && item.end_period >= period);
      cells.push(`<td>${items.map((item) => period === item.start_period
        ? renderMeetingBlock(item, selectedByOption.get(item.option_id))
        : '<div class="class-block continuation" aria-hidden="true"></div>').join("")}</td>`);
    }
    rows.push(`<tr>${cells.join("")}</tr>`);
  }
  const agenda = Array.from({ length: 7 }, (_, offset) => offset + 1).map((weekday) => {
    const dayMeetings = meetings.filter((item) => item.weekday === weekday).sort((left, right) => left.start_period - right.start_period);
    if (!dayMeetings.length) return "";
    return `<section class="agenda-day"><h4>${weekdayNames[weekday]}</h4>${dayMeetings.map((item) => renderAgendaItem(item, selectedByOption.get(item.option_id))).join("")}</section>`;
  }).join("");
  const globalWarnings = (result.warnings || []).map((message) => `<div class="diagnostic persistent-warning">${escapeHtml(humanizePlanMessage(message, plan))}</div>`).join("");
  const planWarnings = (plan.warnings || []).map((message) => `<div class="diagnostic">${escapeHtml(humanizePlanMessage(message, plan))}</div>`).join("");
  const diagnostics = (result.diagnostics || []).map((item) => `<div class="diagnostic">${escapeHtml(humanizePlanMessage(item.message, plan))}</div>`).join("");
  const courseNames = new Map(selectedCourses.map((course) => [course.course_id, `${course.course_code} ${course.course_name}`]));
  (plan.unscheduled_courses || []).forEach((course) => courseNames.set(course.course_id, `${course.course_code} ${course.course_name}`));
  const explanations = (plan.explanations || []).map((item) => {
    const selected = selectedByOption.get(item.selected_option_id);
    const choiceSummary = selected ? formatSelectedChoiceExplanation(selected, plan) : null;
    const messages = (item.messages || [])
      .filter((message) => !selected || !String(message).startsWith("已安排教学班方案 "))
      .map((message) => humanizePlanMessage(message, plan));
    if (choiceSummary) messages.unshift(choiceSummary);
    return `<div class="explanation-row"><strong>${escapeHtml(courseNames.get(item.course_id) || item.course_id)}</strong><span>${messages.map(escapeHtml).join("；") || "暂无补充解释"}</span></div>`;
  }).join("");
  const unscheduled = (plan.unscheduled_courses || []).map((course) => `<div class="diagnostic">未排入：<strong>${escapeHtml(course.course_code)} ${escapeHtml(course.course_name)}</strong>。请查看下方逐课程解释，或放宽对应硬约束。</div>`).join("");
  const phaseLabels = { preselection: "预选", confirmation: "确认", add_drop: "补退选", retake: "重修" };
  $("#plan-results").innerHTML = `<p class="plan-set-status ${planSetSummary.kind}" role="status">${escapeHtml(planSetSummary.text)}</p><div class="plan-tabs" role="tablist" aria-label="候选方案">${tabs}</div><div class="plan-summary"><span class="summary-stat">已排 ${plan.scheduled_course_count ?? plan.selected_option_ids?.length ?? 0} 门</span><span class="summary-stat">未排 ${plan.unscheduled_course_ids?.length ?? 0} 门</span><span class="summary-stat">偏好与风险代价 ${plan.soft_penalty ?? 0}</span><span class="summary-stat">${phaseLabels[result.phase] || "未知"}阶段</span></div><div class="diagnostic-list">${globalWarnings}${planWarnings}${diagnostics}${unscheduled}</div>${renderSelectedCourseSummary(plan)}<div class="schedule-scroll"><table class="schedule-table"><caption class="sr-only">候选课表，按星期和节次排列</caption><thead><tr>${headers}</tr></thead><tbody>${rows.join("")}</tbody></table></div><div class="mobile-agenda" aria-label="移动端课程日程">${agenda || '<div class="empty-state compact"><p>没有精确时段课程；请核对上方已选教学班摘要中的待确认项目。</p></div>'}</div><section class="card explanation-card"><h3>逐课程解释</h3>${explanations || '<p class="muted">暂无课程解释。</p>'}</section>`;
}

function renderMeetingBlock(meeting, selected) {
  const instructors = (selected?.instructors || []).join("、") || "教师待定";
  return `<div class="class-block"><strong>${escapeHtml(meeting.course_name)}</strong><span>教学班 ${escapeHtml(meeting.section_code || "待确认")}</span><span>教师 ${escapeHtml(instructors)}</span><span>${escapeHtml(formatLocation(meeting.campus, meeting.room))}</span><span>${escapeHtml(formatWeeks(meeting.weeks))}</span></div>`;
}

function renderAgendaItem(meeting, selected) {
  const instructors = (selected?.instructors || []).join("、") || "教师待定";
  return `<div class="agenda-item"><span>第 ${meeting.start_period}–${meeting.end_period} 节</span><div><strong>${escapeHtml(meeting.course_name)}</strong><br>教学班 ${escapeHtml(meeting.section_code || "待确认")} · 教师 ${escapeHtml(instructors)}<br>${escapeHtml(formatLocation(meeting.campus, meeting.room))}<br>${escapeHtml(formatWeeks(meeting.weeks))}</div></div>`;
}

function renderSelectedCourseSummary(plan) {
  const selectedCourses = plan.selected_courses || [];
  if (!selectedCourses.length) return "";
  const meetings = plan.meetings || [];
  const cards = selectedCourses.map((selected) => {
    const detailSections = selectedDetailSections(selected);
    const sectionCodes = selectedSectionCodes(selected, detailSections);
    const compositions = [...new Set([
      ...(selected.composition || []),
      ...detailSections.flatMap((section) => section.composition || []),
    ].filter(Boolean))];
    const exactMeetings = meetings.filter((meeting) => meeting.option_id === selected.option_id);
    const detailMeetings = detailSections.flatMap((section) => section.meetings || []);
    const locations = [...new Set([
      ...exactMeetings.map((meeting) => formatLocation(meeting.campus, meeting.room)),
      ...detailMeetings.map((meeting) => formatLocation(meeting.campus, meeting.room)),
    ].filter((location) => location !== "地点待定"))];
    const noExactTime = exactMeetings.length === 0;
    const crossesClass = isCrossAdministrativeClass(compositions);
    return `<article class="result-course-card${crossesClass ? " is-cross-class" : ""}">
      <div class="result-course-title"><span class="course-code">${escapeHtml(selected.course_code)}</span><strong>${escapeHtml(selected.course_name)}</strong></div>
      <dl><div><dt>教学班</dt><dd>${escapeHtml(sectionCodes.join("＋") || "待确认")}</dd></div><div><dt>教师</dt><dd>${escapeHtml((selected.instructors || []).join("、") || "待定")}</dd></div><div><dt>校区 / 地点</dt><dd>${escapeHtml(locations.join("；") || "待确认")}</dd></div><div><dt>教学班组成</dt><dd>${escapeHtml(compositions.join("、") || "未标注")}</dd></div></dl>
      ${noExactTime ? '<p class="unknown-time-note">该教学班没有可放入网格的精确时段；必须结合原始课表核验，不会被视为已完成冲突检查。</p>' : ""}
      ${crossesClass ? `<p class="cross-class-warning"><strong>跨行政班警告：</strong>该班面向 ${escapeHtml(compositions.join("、"))}，未列出你的行政班 ${escapeHtml(state.profile?.administrative_class)}；须到教务系统核验资格。</p>` : ""}
    </article>`;
  }).join("");
  const classContext = state.profile?.administrative_class
    ? `当前行政班：${escapeHtml(state.profile.administrative_class)}`
    : "未填写行政班号，无法自动判断跨班资格";
  return `<section class="card selected-section-summary"><div class="panel-title-row"><div><h3>已选教学班明细</h3><p class="muted">${classContext}</p></div><span class="badge neutral">${selectedCourses.length} 门</span></div><div class="result-course-grid">${cards}</div></section>`;
}

function selectedDetailSections(selected) {
  if (state.currentResultMeta?.catalog_is_stale) return [];
  const detail = state.courseDetails.get(selected.course_id);
  const selectedIds = new Set(selected.section_ids || []);
  return (detail?.sections || []).filter((section) => selectedIds.has(section.id));
}

function selectedSectionCodes(selected, detailSections = selectedDetailSections(selected)) {
  return [...new Set([...(selected.section_codes || []), ...detailSections.map((section) => section.section_code)].filter(Boolean))];
}

function formatSelectedChoiceExplanation(selected, plan) {
  const codes = selectedSectionCodes(selected).join("＋") || "待确认";
  const instructors = (selected.instructors || []).join("、") || "待定";
  const composition = (selected.composition || []).join("、") || "未标注";
  const locations = [...new Set((plan.meetings || [])
    .filter((meeting) => meeting.option_id === selected.option_id)
    .map((meeting) => formatLocation(meeting.campus, meeting.room)))];
  return `已选教学班 ${codes}；教师 ${instructors}；地点 ${locations.join("、") || "待确认"}；教学班组成 ${composition}`;
}

function humanizePlanMessage(message, plan) {
  let text = String(message || "");
  const replacements = [];
  (plan.selected_courses || []).forEach((selected) => {
    const label = `${selected.course_code} ${selected.course_name} 的教学班 ${selectedSectionCodes(selected).join("＋") || "待确认"}`;
    if (selected.option_id) replacements.push([selected.option_id, label]);
    (selected.section_ids || []).forEach((sectionId) => replacements.push([sectionId, label]));
  });
  replacements.sort((left, right) => right[0].length - left[0].length).forEach(([id, label]) => {
    text = text.split(id).join(label);
  });
  return text;
}

function formatWeeks(weeks) {
  if (!weeks?.length) return "周次待定";
  const ranges = [];
  let start = weeks[0], previous = weeks[0];
  for (const week of weeks.slice(1)) {
    if (week === previous + 1) previous = week;
    else { ranges.push(start === previous ? `${start}` : `${start}-${previous}`); start = previous = week; }
  }
  ranges.push(start === previous ? `${start}` : `${start}-${previous}`);
  return `第 ${ranges.join("、")} 周`;
}

function updateRetakeConfirmation() {
  const isRetake = $("#selection-phase").value === "retake";
  $("#retake-confirm-row").classList.toggle("hidden", !isRetake);
  if (!isRetake) $("#retake-confirm").checked = false;
}

function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.setAttribute("role", isError ? "alert" : "status");
  node.setAttribute("aria-live", isError ? "assertive" : "polite");
  node.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = "toast"; }, 3200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function escapeAttribute(value) { return escapeHtml(value); }
