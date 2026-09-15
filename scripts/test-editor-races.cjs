const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function harness(){
 const elements=new Map();const pending=new Map();
 const state={token:'account-a',selectedCourses:new Map(['a','b'].map(id=>[id,{id,name:id,code:id}]))};
 const context={state,toast:()=>{},$:id=>{if(!elements.has(id))elements.set(id,{value:'',classList:{toggle(){}},showModal(){this.open=true;}});return elements.get(id)},ensureCourseDetail:id=>new Promise(r=>pending.set(id,r))};
 vm.createContext(context);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../frontend/course-editor.js'),'utf8')+'\naddEditorSection=()=>{};globalThis.openEditor=openCourseEditor;globalThis.currentId=()=>editingCourseId;',context);
 return {context,state,elements,done:id=>pending.get(id)({sections:[]})};
}
test('rapid clicks on A then B cannot display A while saving into B',async()=>{
 const h=harness();const a=h.context.openEditor('a');const b=h.context.openEditor('b');h.done('b');await b;h.done('a');await a;
 assert.equal(h.elements.get('#custom-name').value,'b');assert.equal(h.context.currentId(),'b');
});
test('late course details cannot reopen editor after switching accounts',async()=>{
 const h=harness();const a=h.context.openEditor('a');h.state.token='account-b';h.state.selectedCourses=new Map();h.done('a');await a;
 assert.notEqual(h.elements.get('#course-editor')?.open,true);
});
test('late course details cannot resurrect a course removed while loading',async()=>{
 const h=harness();const a=h.context.openEditor('a');h.state.selectedCourses.delete('a');h.done('a');await a;
 assert.notEqual(h.elements.get('#course-editor')?.open,true);
});
