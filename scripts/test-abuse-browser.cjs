const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
let browser;
const timer=setTimeout(()=>{console.error('Abuse browser test exceeded 90 seconds');process.exit(1)},90000);
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8877');
 await page.evaluate(async()=>{
  const r=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'abuse_'+Date.now(),password:'isolated-abuse-033'})});
  localStorage.setItem('cugPlannerToken',(await r.json()).access_token);
 });
 await page.reload();await page.locator('#app-shell').waitFor();
 await page.locator('#profile-college').fill('测试学院');await page.locator('#profile-major').fill('测试专业');await page.locator('#profile-cohort').selectOption('2026');await page.locator('#profile-form button[type=submit]').click();
 await page.waitForFunction(()=>state.profile?.cohort_year===2026);
 // Exercise actual editor handlers with late HTTP responses, not just a model of them.
 let releaseA,readyA=false;
 await page.route('**/api/catalog/courses/race-*',async route=>{
  const id=route.request().url().split('/').pop();
  if(id==='race-a'){readyA=true;await new Promise(r=>releaseA=r)}
  await route.fulfill({contentType:'application/json',body:JSON.stringify({id,name:id,code:id,sections:[{id:id+'-s',section_code:'0001',instructors:[],meetings:[{weeks:[1],weekday:1,start_period:1,end_period:2}]}]})});
 });
 await page.evaluate(()=>{for(const id of ['race-a','race-b'])state.selectedCourses.set(id,normalizeCourseSummary({id,code:id,name:id}));void openCourseEditor('race-a')});
 await page.waitForFunction(()=>state.courseDetailRequests.has('race-a'));
 while(!readyA)await page.waitForTimeout(10);
 await page.evaluate(()=>openCourseEditor('race-b'));releaseA();
 await page.waitForFunction(()=>!state.courseDetailRequests.has('race-a'));
 assert.equal(await page.locator('#custom-name').inputValue(),'race-b');
 await page.locator('#custom-name').fill('B编辑后');await page.locator('#course-editor-form button[type=submit]').click();
 await page.waitForFunction(()=>!document.querySelector('#course-editor').open);
 assert.equal(await page.evaluate(()=>state.selectedCourses.get('race-a').name),'race-a');
 assert.equal(await page.evaluate(()=>[...state.selectedCourses.values()].some(c=>c.name==='B编辑后')),true);
 await page.evaluate(()=>{state.selectedCourses.clear();state.courseDetails.clear();renderSelectedCourses()});
 // Markup-looking names must remain text; zero/negative/invalid times cannot be saved.
 await page.locator('#open-manual-editor').click();await page.locator('#custom-name').fill('<img src=x onerror=alert(1)>');
 for(const bad of ['0','65','8-1','1-5,','1-4,unknown']){
  await page.locator('[data-weeks]').fill(bad);await page.locator('[data-weekday]').selectOption('1');await page.locator('[data-start]').fill('1');await page.locator('[data-end]').fill('2');
  await page.locator('#course-editor-form button[type=submit]').click();assert.equal(await page.evaluate(()=>state.selectedCourses.size),0);
 }
 await page.locator('[data-weeks]').fill('1-8');await page.locator('[data-start]').fill('0');await page.locator('#course-editor-form button[type=submit]').click();assert.equal(await page.evaluate(()=>state.selectedCourses.size),0);
 await page.locator('[data-start]').fill('1');await page.locator('[data-non-blocking]').check();await page.locator('[data-copy-meeting]').click();
 assert.equal(await page.locator('[data-non-blocking]:checked').count(),2);
 await page.locator('#course-editor-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#course-editor').open);
 assert.equal(await page.locator('#selected-courses img').count(),0);
 assert.equal(await page.evaluate(()=>[...state.selectedCourses.values()][0].custom.sections[0].meetings.every(m=>m.non_blocking)),true);
 await page.locator('#generate-plan').click();await page.waitForFunction(()=>!document.querySelector('#generate-plan').disabled);
 assert.equal(await page.evaluate(()=>state.planResponse.plans[0].scheduled_course_count),1);
 assert.deepEqual(errors,[]);
 fs.writeFileSync('tmp/abuse-033/browser.json',JSON.stringify({rapid_editor_late_HTTP_response:'pass',saved_correct_course:'pass',invalid_week_cases:5,zero_period_rejected:true,markup_name_inert:true,copied_practice_retains_type:true,page_errors:errors},null,2));
 console.log('PASS: real Edge misuse tests');await browser.close();clearTimeout(timer);
})().catch(async e=>{console.error(e);clearTimeout(timer);if(browser)await browser.close();process.exit(1)});
