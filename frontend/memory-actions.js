"use strict";
let memoryActionBusy = false;
document.addEventListener('DOMContentLoaded',()=>{
  $('#save-draft-now').addEventListener('click',async()=>{
    if(memoryActionBusy || state.draftRestorePending) return;
    if(state.draftWriteBlocked)return toast('旧草稿需要先处理，请按草稿提示操作或删除旧草稿',true);
    const button=$('#save-draft-now');button.disabled=true;
    try{const saved=await savePlanningDraft();if(saved)toast('草稿已保存');}finally{button.disabled=false;}
  });
  $('#delete-draft').addEventListener('click',deleteCurrentDraft);
  $('#clear-history').addEventListener('click',()=>deleteHistoryRecord(null));
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-delete-history]');
    if(button) void deleteHistoryRecord(button.dataset.deleteHistory);
  });
});
function clearDisplayedPlan(){
  state.resultRequestId++;
  state.planResponse=null;state.currentResultMeta=null;
  $('#plan-results').innerHTML='<div class="empty-state"><p>选择课程并点击排课，或从历史记录打开保留的方案。</p></div>';
  $('#adjustment-results').innerHTML='';$('#more-plans').classList.add('hidden');
  $('#result-stale-alert').classList.add('hidden');
}
async function deleteCurrentDraft(){
  if(memoryActionBusy || !state.token) return;
  if(state.draftRestorePending || state.draftRestoreInProgress || $('#import-direct-courses').disabled || $('#import-catalog').disabled) return toast('正在恢复草稿或导入课程，请等待完成后再删除',true);
  if($('#generate-plan').disabled) return toast('正在排课，请等待完成后再删除草稿',true);
  if(!confirm('删除当前账号的草稿，并清空页面上的待排课程和排课偏好？\n保留学生信息、历史方案和课程总库。此操作不可撤销。'))return;
  const token=state.token,wasBlocked=state.draftWriteBlocked;
  memoryActionBusy=true;state.draftWriteBlocked=true;clearTimeout(state.draftSaveTimer);
  state.draftSaveTimer=null;$('#app-shell').inert=true;
  try{
    // Drain queued/in-flight PUTs before DELETE; otherwise an old save can recreate it.
    await draftSaveTail.catch(()=>null);
    if(token!==state.token)return;
    await api('/api/plans/draft',{method:'DELETE'});
    state.selectedCourses.clear();state.courseDetails.clear();state.courseDetailRequests.clear();
    state.blockedTimes=[];state.instructorRules=[];
    for(const id of ['#prefer-no-early','#prefer-no-evening','#prefer-compact','#retake-confirm'])$(id).checked=false;
    $('#selection-phase').value='planning';$('#retake-confirm-row').classList.add('hidden');
    $('#blocked-time-form').reset();$('#teacher-rule-form').reset();
    $('#course-editor').close();editingCourseId=null;courseEditorRequestId++;
    renderSelectedCourses();renderRules();clearDisplayedPlan();
    state.draftWriteBlocked=false;
    $('#draft-save-status').textContent='草稿已删除；添加课程或修改偏好后会创建新草稿';
    toast('草稿已删除，历史方案和总库已保留');
  }catch(error){if(!error.staleSession){state.draftWriteBlocked=wasBlocked;toast(`草稿删除失败：${error.message}`,true);}}
  finally{memoryActionBusy=false;$('#app-shell').inert=false;}
}
async function deleteHistoryRecord(runId){
  if(memoryActionBusy || !state.token)return;
  if($('#generate-plan').disabled)return toast('正在生成新方案，请等待完成后再删除历史',true);
  if(!confirm(runId?'删除这条历史记录及其中的候选方案？\n不会删除当前草稿或总库，删除后不可恢复。':'删除当前账号的全部历史记录（包括未显示的较早记录）？\n不会删除草稿或总库，删除后不可恢复。'))return;
  memoryActionBusy=true;$('#app-shell').inert=true;
  historyRequestId++;state.resultRequestId++;
  try{
    await api('/api/plans/history'+(runId?'/'+encodeURIComponent(runId):''),{method:'DELETE'});
    clearDisplayedPlan();
    await loadPlanningHistory();toast(runId?'这条历史方案已删除':'当前账号的历史方案已全部删除');
  }catch(error){if(!error.staleSession){toast(`删除失败：${error.message}`,true);await loadPlanningHistory();}}
  finally{memoryActionBusy=false;$('#app-shell').inert=false;}
}
