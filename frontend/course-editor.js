"use strict";

let editingCourseId = null;
function bindExtendedCourseEvents() {
  $$('[data-open-help]').forEach(button=>button.addEventListener('click',()=>{
    if (!$("#help-frame").getAttribute('src')) $("#help-frame").src='/help.html';
    $("#help-dialog").showModal();
  }));
  $("#close-help").addEventListener('click',()=>$("#help-dialog").close());
  $("#download-course-template").addEventListener('click', async()=>{
    try {
      const response = await fetch('/api/catalog/template', {headers:{Authorization:`Bearer ${state.token}`}});
      if (!response.ok) throw new Error('模板下载失败，请重新登录后重试');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');link.href=url;link.download='课石_课程课表模板.xlsx';link.click();
      setTimeout(()=>URL.revokeObjectURL(url),10000);
    } catch(error) {toast(error.message,true);}
  });
  $("#open-manual-editor").addEventListener("click", () => openCourseEditor());
  $("#open-import-workbooks").addEventListener("click", () => $("#direct-course-files").click());
  for (const selector of ["#target-year", "#target-season", "#profile-semester"]) $(selector).addEventListener("change", updateSemesterNote);
  for (let year = 2027; year <= 2100; year++) $("#profile-cohort").add(new Option(`${year} 级`, year));
  for (let semester = 2; semester <= 16; semester += 2) $("#profile-semester").add(new Option(`第 ${semester} 学期`, semester));
  $("#close-course-editor").addEventListener("click", () => $("#course-editor").close());
  $("#add-custom-section").addEventListener("click", () => addEditorSection());
  $("#course-editor-form").addEventListener("submit", saveCourseEditor);
  $("#custom-sections").addEventListener("change",event=>{
    if(event.target.matches('[data-non-blocking]')) updateMeetingFields(event.target.closest('.editor-meeting'));
  });
  $("#custom-sections").addEventListener("click", (event) => {
    const section = event.target.closest(".editor-section");
    if (!section) return;
    if (event.target.matches("[data-add-meeting]")) addEditorMeeting(section);
    if (event.target.matches("[data-remove-meeting]")) event.target.closest(".editor-meeting").remove();
    if (event.target.matches("[data-copy-meeting]")) {
      const original = event.target.closest('.editor-meeting');
      const copy = original.cloneNode(true);
      [...original.querySelectorAll('input,select')].forEach((input,i)=>copy.querySelectorAll('input,select')[i].value=input.value);
      original.after(copy);
    }
    if (event.target.matches("[data-remove-custom-section]")) { section.remove(); refreshMergeTargets(); }
    if (event.target.matches("[data-merge-section]")) {
      const targetId = section.querySelector('[data-merge-target]').value;
      const target = [...$("#custom-sections").children].find(s=>s.dataset.sectionId===targetId);
      if (!target) return toast("请选择要并入的组", true);
      [...section.querySelectorAll('.editor-meeting')].forEach(row=>target.querySelector('.editor-meetings').append(row));
      target.querySelector('[data-section-name]').value = [target,section].map(s=>s.querySelector('[data-section-name]').value).filter(Boolean).join('+');
      target.querySelector('[data-instructors]').value = [...new Set([target,section].flatMap(s=>s.querySelector('[data-instructors]').value.split(/[,，、;；]/)).filter(Boolean))].join('、');
      section.remove(); refreshMergeTargets();
    }
  });
  $("#import-catalog").addEventListener("click", () => uploadCourseFiles(true));
  $("#import-direct-courses").addEventListener("click", () => uploadCourseFiles(false));
  $("#delete-catalog").addEventListener("click", async () => {
    try {
      await api("/api/catalog", {method: "DELETE"});
      await catalogChanged();
      $("#catalog-import-status").textContent = "课程总库已删除；账号、自填课程和历史方案已保留。";
    } catch (error) { if (error.staleSession) return; toast(error.message, true); }
  });
  $("#restore-builtin-catalog").addEventListener("click", async () => {
    try {
      const result = await api('/api/catalog/restore-builtin', {method:'POST'});
      await catalogChanged();
      $("#catalog-import-status").textContent = `已恢复内置 ${result.courses} 门课程、${result.sections} 个教学班。`;
    } catch (error) { if (error.staleSession) return; toast(error.message, true); }
  });
  $("#more-plans").addEventListener("click", () => generatePlan(Math.min(100, (state.planResponse?.plan_limit || 10) + 10)));
}

function customCourseDetail(course) {
  return {...course, name: course.custom.name, code: course.custom.code || "自填",
    sections: course.custom.sections.map((section) => ({...section,
      id: `${course.id}:${section.id}`, display_name: section.section_code,
      meetings: section.meetings.map((m) => ({...m, precision: m.non_blocking ? "non_blocking" : "exact_slot"})),
      composition: [], issues: [], default_eligible: true, needs_confirmation: false,
      enrolled_count: null, capacity: null,
    }))};
}

async function openCourseEditor(courseId = null) {
  editingCourseId = courseId;
  const course = courseId ? state.selectedCourses.get(courseId) : null;
  let custom = course?.custom;
  if (course && !custom) {
    const detail = await ensureCourseDetail(courseId);
    if (!detail) return toast("无法读取课程，请重试", true);
    custom = {name: course.name, code: course.code, component_relationship_confirmed: !detail.component_review_required, sections: detail.sections.map((s, i) => ({
      id: String(i), section_code: s.section_code, instructors: s.instructors, meetings: s.meetings,
    }))};
  }
  $("#custom-name").value = custom?.name || "";
  $("#custom-code").value = custom?.code || "";
  $("#custom-error").textContent = "";
  $("#custom-sections").innerHTML = "";
  (custom?.sections || [{}]).forEach(addEditorSection);
  const review = custom?.component_relationship_confirmed === false;
  $("#component-confirm-row").classList.toggle('hidden', !review);
  $("#component-confirm").checked = !review;
  $("#course-editor").showModal();
}

function addEditorSection(value = {}) {
  const section = document.createElement("section");
  section.className = "editor-section card";
  section.dataset.sectionId = value.id || crypto.randomUUID();
  section.innerHTML = `<div class="two-columns"><label>教学班名称（选填）<input data-section-name maxlength="128" value="${escapeAttribute(value.section_code || "")}" /></label><label>教师（选填，多位用逗号分隔）<input data-instructors value="${escapeAttribute((value.instructors || []).join("、"))}" /></label></div><div class="editor-meetings"></div><div class="inline-actions"><button type="button" data-add-meeting class="text-button">添加上课时段</button><button type="button" data-remove-custom-section class="text-button">删除这个教学班</button></div>`;
  $("#custom-sections").append(section);
  const merge = document.createElement('div');
  merge.className = 'inline-actions';
  merge.innerHTML = '<label>必须与另一组一起上时<select data-merge-target><option value="">选择目标组</option></select></label><button type="button" class="text-button" data-merge-section>将本组并入所选组</button>';
  section.append(merge);
  (value.meetings?.length ? value.meetings : [{}]).forEach(m => addEditorMeeting(section, m));
  refreshMergeTargets();
}

function refreshMergeTargets() {
  const sections = [...$("#custom-sections").children];
  for (const section of sections) {
    section.querySelector('[data-merge-target]').innerHTML = '<option value="">选择目标组</option>' + sections.filter(s=>s!==section).map(s=>`<option value="${escapeAttribute(s.dataset.sectionId)}">${escapeHtml(s.querySelector('[data-section-name]').value || `第 ${sections.indexOf(s)+1} 组`)}</option>`).join('');
  }
}

function addEditorMeeting(section, value = {}) {
  const row = document.createElement("div");
  row.className = "editor-meeting";
  row.innerHTML = `<label>周次<input data-weeks required placeholder="如 1-5,7-8 或 1-16周(单)" value="${escapeAttribute((value.weeks || []).join(","))}" /></label><div class="three-columns"><label>星期<select data-weekday required><option value="">请选择</option>${weekdayNames.slice(1).map((name, i) => `<option value="${i + 1}" ${value.weekday === i + 1 ? "selected" : ""}>${name}</option>`).join("")}</select></label><label>开始节次<input data-start type="number" min="1" max="20" required value="${value.start_period || ""}" /></label><label>结束节次<input data-end type="number" min="1" max="20" required value="${value.end_period || ""}" /></label></div><div class="two-columns"><label>教室（选填）<input data-room maxlength="128" value="${escapeAttribute(value.room || "")}" /></label><label>校区（选填）<input data-campus maxlength="128" value="${escapeAttribute(value.campus || "")}" /></label></div><button type="button" data-remove-meeting class="text-button">删除这个时段</button>`;
  section.querySelector(".editor-meetings").append(row);
  row.insertAdjacentHTML('afterbegin','<div class="editor-time-title"><strong>上课时段</strong><button class="text-button" type="button" data-copy-meeting>复制时段</button></div>');
  row.insertAdjacentHTML('afterbegin',`<label class="toggle-row"><input type="checkbox" data-non-blocking ${value.non_blocking || value.precision==='non_blocking' ? 'checked' : ''} /><span>实践、实习或课程设计：时间未定，只记录周次，不参与冲突</span></label>`);
  updateMeetingFields(row);
}

function updateMeetingFields(row) {
  const nonBlocking = row.querySelector('[data-non-blocking]').checked;
  for(const input of row.querySelectorAll('[data-weekday],[data-start],[data-end]')) {input.disabled=nonBlocking;input.required=!nonBlocking;}
  row.querySelector('[data-weeks]').required=!nonBlocking;
}

function parseTeachingWeeks(value, maxWeek = 64) {
  const result = new Set();
  const normalized = value.trim().replace(/[，、；;]/g, ",").replace(/[－—–~～至]/g, "-").replace(/第/g, "").replace(/\s/g, "").replace(/（/g,"(").replace(/）/g,")");
  for (const part of normalized.split(",")) {
    const match = /^(\d+)(?:-(\d+))?周?(?:\((单|双)周?\))?$/.exec(part);
    if (!match) throw new Error(`无法识别周次“${part}”，请填写如 1-5,7-8 或 1-16周(单)`);
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end > maxWeek) throw new Error(`周次须在 1–${maxWeek}，结束周不能早于开始周`);
    for (let w = start; w <= end; w++) if (!match[3] || (w % 2 === (match[3] === "单" ? 1 : 0))) result.add(w);
  }
  if (!result.size) throw new Error("至少选择一个教学周");
  return [...result].sort((a,b) => a-b);
}

async function saveCourseEditor(event) {
  event.preventDefault();
  try {
    const name = $("#custom-name").value.trim();
    if (!name) throw new Error("请填写课程名称");
    const sections = [...$("#custom-sections").children].map((section, index) => {
      const meetings = [...section.querySelectorAll(".editor-meeting")].map(row => {
        const get = selector => row.querySelector(selector).value;
        const start = Number(get("[data-start]")), end = Number(get("[data-end]"));
        const nonBlocking=row.querySelector('[data-non-blocking]').checked;
        if (!nonBlocking && end < start) throw new Error("结束节次不能早于开始节次");
        return {non_blocking:nonBlocking,weeks:nonBlocking && !get('[data-weeks]').trim() ? [] : parseTeachingWeeks(get("[data-weeks]")), weekday:nonBlocking ? null : Number(get("[data-weekday]")),
          start_period:nonBlocking ? null : start, end_period:nonBlocking ? null : end, room: get("[data-room]") || null, campus: get("[data-campus]") || null};
      });
      if (!meetings.length) throw new Error("每个教学班至少填写一个完整的上课时段");
      return {id: section.dataset.sectionId, section_code: section.querySelector("[data-section-name]").value.trim() || `自填班 ${index + 1}`,
        instructors: section.querySelector("[data-instructors]").value.split(/[,，、;；]/).map(s=>s.trim()).filter(Boolean), meetings};
    });
    if (!sections.length) throw new Error("至少添加一个教学班");
    if (!$("#component-confirm").checked) throw new Error("请先核对并确认教学班的组成关系");
    const original = state.selectedCourses.get(editingCourseId);
    const id = original?.custom ? editingCourseId : `custom:${crypto.randomUUID()}`;
    const custom = {name, code: $("#custom-code").value.trim() || "自填", component_relationship_confirmed: true, sections};
    if (custom.code !== '自填' && [...state.selectedCourses.values()].some(c=>c.id!==editingCourseId && c.code?.trim().toLowerCase()===custom.code.toLowerCase())) throw new Error('相同课程号已经在待排列表，请编辑已有课程并添加备选教学班');
    if (editingCourseId) state.selectedCourses.delete(editingCourseId);
    state.selectedCourses.set(id, normalizeCourseSummary({id, custom, name, code: custom.code,
      priority: original?.priority ?? 100, required: original?.required || false, section_count: sections.length,
      locked_section_id: original?.custom ? original.locked_section_id : null,
      forbidden_section_ids: original?.custom ? original.forbidden_section_ids : []}));
    const course = state.selectedCourses.get(id);
    await ensureCourseDetail(id);
    const validIds = new Set(state.courseDetails.get(id).sections.map(s=>s.id));
    if (!validIds.has(course.locked_section_id)) course.locked_section_id = null;
    course.forbidden_section_ids = course.forbidden_section_ids.filter(s=>validIds.has(s));
    renderSelectedCourses(); scheduleDraftSave(); $("#course-editor").close();
    toast("课程已保存，请重新排课以使用修改后的时间");
  } catch (error) { if (error.staleSession) return; $("#custom-error").textContent = error.message; }
}

async function catalogChanged() {
  state.courseDetails = new Map(); state.courseDetailRequests = new Map();
  await loadCatalogStatus();
  await Promise.all([...state.selectedCourses.keys()].map(id => ensureCourseDetail(id)));
  renderSelectedCourses();
  showStaleAlert("课程总库已变更，请核对待排课程并重新求解；历史方案保留原有快照。");

}

async function uploadCourseFiles(archives) {
  const input = $(archives ? "#catalog-files" : "#direct-course-files");
  const button = $(archives ? "#import-catalog" : "#import-direct-courses");
  const max = archives ? 5 : 20;
  if (!input.files.length || input.files.length > max) return toast(`请选择 1–${max} 个文件`, true);
  const data = new FormData();
  for (const file of input.files) data.append("files", file);
  button.disabled = true;
  const status = $(archives ? '#catalog-import-status' : '#direct-import-status');
  status.textContent = `正在读取 ${input.files.length} 个文件，请稍候…`;
  try {
    const result = await api(archives ? "/api/catalog/import" : "/api/catalog/import-courses", {method:"POST", body:data});
    if (archives) {
      await catalogChanged();
      $("#catalog-import-status").textContent = `已读取 ${result.file_count} 个表格，建立 ${result.courses} 门课程、${result.sections} 个教学班的总库。`;
      if (result.warnings?.length) status.textContent += '\n' + result.warnings.join('\n');
    } else {
      for (const choice of result.courses) {
        const existing = [...state.selectedCourses.values()].find(c=>c.code?.trim().toLowerCase()===choice.custom.code.trim().toLowerCase());
        if (existing) state.selectedCourses.delete(existing.id);
        const course = normalizeCourseSummary({id:choice.course_id, ...choice, name:choice.custom.name,
          code:choice.custom.code, section_count:choice.custom.sections.length,
          required: existing?.required || choice.required, priority: existing?.priority ?? choice.priority});
        state.selectedCourses.set(course.id, course);
        await ensureCourseDetail(course.id);
      }
      updateInputModeUi(); renderSelectedCourses(); scheduleDraftSave();
      toast(`已载入 ${result.courses.length} 门课程；同课程号的待排记录已更新，可设置偏好后排课`);
      status.textContent = `已读取 ${result.file_count} 个文件，载入 ${result.courses.length} 门课程。同课程号的旧待排记录已更新。`;
    }
  } catch (error) { if (error.staleSession) return; status.textContent = `导入未完成：${error.message}`; toast(error.message, true); }
  finally { button.disabled = false; }
}

function renderAdjustment(adjustment) {
  const container = $("#adjustment-results");
  if (!adjustment) {container.innerHTML = ""; return;}
  const leaves = adjustment.leaves || [], removals = adjustment.remove_courses || [];
  container.innerHTML = `<section class="card"><h3>${removals.length ? "建议调整选课" : "请假调整建议"}</h3><p>${escapeHtml(adjustment.message || "")}</p>${adjustment.kind === "leave" || adjustment.kind === "drop_courses" ? `<p>${adjustment.optimal ? "已证明当前限制下的最小调整" : "找到可用调整，尚未证明最少"}：删 ${removals.length} 门课，请假 ${leaves.length} 次。</p>` : ""}${adjustment.full_schedule_minimum_leave_count > 10 ? `<p>保留全部课程最少需要请假 ${adjustment.full_schedule_minimum_leave_count} 次，超过 10 次。</p>` : ""}<ul>${removals.map(r=>`<li>建议移除：${escapeHtml(r.course_name)}</li>`).join("")}${leaves.map(r=>`<li>${escapeHtml(r.course_name)}：第 ${r.week} 周${weekdayNames[r.weekday]}第 ${r.start_period}–${r.end_period} 节（1 次）</li>`).join("")}</ul><p>建议不会自动删除课程或将请假视为已获准。请核对学校和课程要求。</p></section>`;
  if (adjustment.selected_courses?.length) container.querySelector('section').insertAdjacentHTML('beforeend', `<details><summary>查看这项建议采用的教学班</summary><ul>${adjustment.selected_courses.map(c=>`<li>${escapeHtml(c.course_name)} · ${escapeHtml(c.section_code)} · ${escapeHtml(c.instructors.join('、') || '教师未填')}</li>`).join('')}</ul></details>`);
}
