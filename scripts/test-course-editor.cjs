const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../frontend/course-editor.js'),'utf8')+'\nglobalThis.parseWeeksForTest=parseTeachingWeeks;',context);
const parse=s=>Array.from(context.parseWeeksForTest(s));
test('screenshot discontinuous week segments retain their exact weeks',()=>{
 assert.deepEqual(parse('第1-5周,第7-8周'),[1,2,3,4,5,7,8]);
 assert.deepEqual(parse('1-3，6-8'),[1,2,3,6,7,8]);
});
test('odd and even weeks, full-width punctuation and duplicates',()=>{
 assert.deepEqual(parse('1-8周（单）'),[1,3,5,7]);
 assert.deepEqual(parse('1-8周(双),4'),[2,4,6,8]);
});
test('bad or empty weeks are rejected rather than partially interpreted',()=>{
 for(const input of ['', '1-5,', '8-1','0-4','65','1-4,unknown']) assert.throws(()=>parse(input));
});
