"use strict";
let accountActionPending=false;

document.addEventListener('DOMContentLoaded',()=>{
  for(const id of ['#account-settings','#reset-password-dialog']){
    $(id).addEventListener('cancel',event=>{if(accountActionPending)event.preventDefault();});
  }
  $('#open-account-settings').addEventListener('click',()=>{
    clearAccountForms();$('#account-settings').showModal();
  });
  $('#close-account-settings').addEventListener('click',()=>$('#account-settings').close());
  $('#account-settings').addEventListener('close',clearAccountForms);
  $('#forgot-password').addEventListener('click',()=>{
    $('#reset-password-form').reset();$('#reset-password-error').textContent='';
    $('#reset-username').value=$('#auth-username').value.trim();$('#reset-password-dialog').showModal();
  });
  $('#close-reset-password').addEventListener('click',()=>$('#reset-password-dialog').close());
  $('#reset-password-dialog').addEventListener('close',()=>$('#reset-password-form').reset());
  $('#change-password-form').addEventListener('submit',changeAccountPassword);
  $('#generate-recovery-form').addEventListener('submit',generateAccountRecoveryCode);
  $('#reset-password-form').addEventListener('submit',recoverAccountPassword);
  $('#recovery-code-saved').addEventListener('change',()=>$('#finish-recovery-code').disabled=!$('#recovery-code-saved').checked);
  $('#finish-recovery-code').addEventListener('click',()=>{
    if($('#recovery-code-saved').checked){$('#recovery-code-value').value='';$('#recovery-code-dialog').close();}
  });
  $('#recovery-code-dialog').addEventListener('cancel',event=>{event.preventDefault();toast('请先保存恢复码，再勾选确认');});
  $('#copy-recovery-code').addEventListener('click',async()=>{
    const input=$('#recovery-code-value');
    try{await navigator.clipboard.writeText(input.value);toast('恢复码已复制，请保存到安全位置');}
    catch{input.focus();input.select();toast('请按Ctrl+C复制选中的恢复码');}
  });
  $('#download-recovery-code').addEventListener('click',()=>{
    const content='课石本地账号恢复文件\n'+$('#recovery-code-account').textContent+'\n恢复码：'+$('#recovery-code-value').value+'\n\n请勿分享。仅配合原账号数据库或完整备份使用；使用恢复码重置后旧码失效，请保存新的恢复码。';
    const url=URL.createObjectURL(new Blob(['\ufeff'+content],{type:'text/plain;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='课石-账号恢复码.txt';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),300000);
  });
});

function clearAccountForms(){
  $('#change-password-form').reset();$('#generate-recovery-form').reset();
  $('#change-password-error').textContent='';$('#generate-recovery-error').textContent='';
}
function clearAccountUi(){
  for(const id of ['#account-settings','#reset-password-dialog','#recovery-code-dialog'])$(id).close();
  clearAccountForms();$('#reset-password-form').reset();$('#recovery-code-value').value='';
  $('#recovery-code-account').textContent='';$('#account-recovery-status').textContent='';
}
function updateAccountUi(me){
  $('#account-recovery-status').textContent=me.recovery_enabled?'已设置离线恢复码。保管好已保存的恢复文件；遗失时可验证密码后生成新码。':'尚未设置恢复码，请验证当前密码后生成并保存。';
}
function showRecoveryCode(code,username){
  $('#recovery-code-value').value=code;$('#recovery-code-account').textContent='账号：'+username;
  $('#recovery-code-saved').checked=false;$('#finish-recovery-code').disabled=true;
  $('#recovery-code-dialog').showModal();
}
function checkNewPassword(password,confirmation){
  if(password!==confirmation)throw new Error('两次输入的新密码不一致');
  if(password.length<10)throw new Error('新密码至少需要10个字符');
}
async function changeAccountPassword(event){
  event.preventDefault();if(accountActionPending)return;
  const dialog=$('#account-settings'),error=$('#change-password-error');error.textContent='';
  try{
    const password=$('#change-new-password').value;
    checkNewPassword(password,$('#change-confirm-password').value);
    accountActionPending=true;dialog.inert=true;
    if(state.draftRevision!==state.savedDraftRevision){
      const saved=await savePlanningDraft({silent:true});
      if(!saved)throw new Error('草稿还未保存成功，请先保存草稿后再修改密码');
    }
    const username=state.account?.username || $('#current-user').textContent;
    await api('/api/auth/change-password',{method:'POST',body:JSON.stringify({current_password:$('#change-current-password').value,new_password:password})});
    clearSession();showAuth();setAuthMode('login');$('#auth-username').value=username;
    toast('密码已修改，课表数据保留。请用新密码重新登录');
  }catch(e){if(!e.staleSession)error.textContent=e.message;}
  finally{accountActionPending=false;dialog.inert=false;}
}
async function generateAccountRecoveryCode(event){
  event.preventDefault();if(accountActionPending)return;
  if(!confirm('生成新恢复码后，旧恢复码立即失效。请保存即将显示的新码。继续吗？'))return;
  const dialog=$('#account-settings'),error=$('#generate-recovery-error');error.textContent='';
  accountActionPending=true;dialog.inert=true;
  try{
    const response=await api('/api/auth/recovery-code',{method:'POST',body:JSON.stringify({current_password:$('#recovery-current-password').value})});
    const username=state.account?.username || $('#current-user').textContent;
    if(state.account)state.account.recovery_enabled=true;
    updateAccountUi({recovery_enabled:true});dialog.close();showRecoveryCode(response.recovery_code,username);
  }catch(e){if(!e.staleSession)error.textContent=e.message;}
  finally{accountActionPending=false;dialog.inert=false;}
}
async function recoverAccountPassword(event){
  event.preventDefault();if(accountActionPending)return;
  const dialog=$('#reset-password-dialog'),error=$('#reset-password-error');error.textContent='';
  try{
    const password=$('#reset-new-password').value;checkNewPassword(password,$('#reset-confirm-password').value);
    const username=$('#reset-username').value.trim();accountActionPending=true;dialog.inert=true;
    const response=await api('/api/auth/reset-password',{method:'POST',body:JSON.stringify({username,recovery_code:$('#reset-recovery-code').value,new_password:password})});
    dialog.close();clearSession();showAuth();setAuthMode('login');$('#auth-username').value=username;
    showRecoveryCode(response.recovery_code,username);
    toast('密码已重置，旧恢复码已失效。请保存新恢复码，再用新密码登录');
  }catch(e){if(!e.staleSession)error.textContent=e.message;}
  finally{accountActionPending=false;dialog.inert=false;}
}
