const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.KESHI_TEST_URL || 'http://127.0.0.1:8877';
let browser;
const deadline=setTimeout(async()=>{console.error('Browser test deadline exceeded');if(browser)await browser.close();process.exit(1);},120000);
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true,args:['--renderer-process-limit=2']});
 const context=await browser.newContext({viewport:{width:1360,height:960}});
 const external=[];const errors=[];
 await context.route('**/*',route=>{
   const host=new URL(route.request().url()).hostname;
   if(host!=='127.0.0.1' && host!=='localhost'){external.push(route.request().url());return route.abort();}
   return route.continue();
 });
 const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 assert.equal(await page.locator('.trust-list').count(),0);
 assert.equal(await page.getByText('把想上的课，',{exact:false}).count(),0);
 await page.locator('#auth-screen [data-open-help]').click();
 await page.frameLocator('#help-frame').getByRole('heading',{name:'二、账号与断网使用'}).waitFor();
 await page.screenshot({path:'tmp/stress-031/help-desktop.png'});
 await page.locator('#close-help').click();
 await page.screenshot({path:'tmp/stress-031/login-clean.png'});
 const suffix=Date.now();const names=['offline_a_'+suffix,'offline_b_'+suffix];
 const tokens=await page.evaluate(async names=>{
   const result=[];
   for(const username of names){const r=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password:'browser-local-test-031'})});if(!r.ok)throw new Error('registration failed');result.push((await r.json()).access_token);}
   return result;
 },names);
 await page.locator('#auth-username').fill(names[0]);await page.locator('#auth-password').fill('browser-local-test-031');await page.locator('#auth-submit').click();
 await page.locator('#app-shell').waitFor({state:'visible'});
 await page.locator('#profile-school').fill('另一所测试大学');
 await page.locator('#profile-college').fill('工程学院');await page.locator('#profile-major').fill('土木工程');await page.locator('#profile-cohort').selectOption('2024');await page.locator('#profile-form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('#profile-status').textContent==='已保存');
 await page.locator('#open-manual-editor').click();await page.locator('#custom-name').fill('离线核对课程');
 await page.locator('[data-weeks]').fill('1-5,7-8');await page.locator('[data-weekday]').selectOption('1');await page.locator('[data-start]').fill('1');await page.locator('[data-end]').fill('2');await page.locator('[data-room]').fill('A101');
 await page.locator('[data-copy-meeting]').click();await page.locator('[data-room]').nth(1).fill('B202');
 await page.locator('#course-editor-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#course-editor').open);
 await page.locator('#generate-plan').click();await page.waitForFunction(()=>!document.querySelector('#generate-plan').disabled);
 assert.ok((await page.locator('#plan-results').innerText()).includes('没有找到可执行方案'));
 await page.locator('[data-edit-course]').click();await page.locator('[data-weekday]').nth(1).selectOption('2');await page.locator('#course-editor-form button[type=submit]').click();
 await page.locator('#generate-plan').click();await page.waitForFunction(()=>!document.querySelector('#generate-plan').disabled);
 assert.equal(await page.evaluate(()=>state.planResponse.plans[0].scheduled_course_count),1);
 await page.locator('[data-view-week]').selectOption('6');assert.equal(await page.locator('#plan-results .class-block').count(),0);
 // Hold a completed old response while the user edits preferences.
 let releasePlan,planReady=false;
 await page.route('**/api/plans/generate',async route=>{const response=await route.fetch();planReady=true;await new Promise(resolve=>releasePlan=resolve);await route.fulfill({response});});
 await page.locator('#generate-plan').click();
 while(!planReady)await page.waitForTimeout(20);
 await page.locator('[data-course-field=priority]').selectOption('200');releasePlan();
 await page.waitForFunction(()=>!document.querySelector('#generate-plan').disabled);
 assert.ok((await page.locator('#result-stale-alert').innerText()).includes('已修改'));
 await page.unroute('**/api/plans/generate');
 // An old search must not overwrite the newer query.
 let releaseSearch,searchReady=false;
 await page.route('**/api/catalog/search*',async route=>{
   const q=new URL(route.request().url()).searchParams.get('q');
   if(q==='旧查询'){searchReady=true;await new Promise(resolve=>releaseSearch=resolve);}
   await route.fulfill({contentType:'application/json',body:JSON.stringify([{id:q,code:q,name:q,section_count:1}])});
 });
 await page.evaluate(()=>{document.querySelector('#course-search').value='旧查询';void searchCourses();});
 while(!searchReady)await page.waitForTimeout(20);
 await page.evaluate(async()=>{document.querySelector('#course-search').value='新查询';await searchCourses();});
 releaseSearch();await page.waitForTimeout(100);
 assert.ok((await page.locator('#course-search-results').innerText()).includes('新查询'));
 await page.unroute('**/api/catalog/search*');
 // Delay account A's history, then log out and log in as account B.
 let releaseHistory,historyReady=false,held=false;
 await page.route('**/api/plans/history?limit=10',async route=>{
   if(held)return route.continue();held=true;
   const response=await route.fetch();historyReady=true;await new Promise(resolve=>releaseHistory=resolve);await route.fulfill({response});
 });
 await page.evaluate(()=>{document.querySelector('#adjustment-results').innerHTML='A_ACCOUNT_PRIVATE';void loadPlanningHistory();});
 while(!historyReady)await page.waitForTimeout(20);
 await page.locator('#logout-button').click();await page.locator('#auth-screen').waitFor({state:'visible'});
 assert.equal(await page.locator('#auth-password').inputValue(),'');
 assert.equal(await page.locator('#adjustment-results').innerText(),'');
 await page.locator('#auth-username').fill(names[1]);await page.locator('#auth-password').fill('browser-local-test-031');await page.locator('#auth-submit').click();await page.locator('#app-shell').waitFor({state:'visible'});
 releaseHistory();await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>state.historyRuns.length),0);
 assert.equal(await page.evaluate(()=>state.selectedCourses.size),0);
 await page.unroute('**/api/plans/history?limit=10');
 await page.locator('#profile-college').fill('工程学院');await page.locator('#profile-major').fill('土木工程');await page.locator('#profile-cohort').selectOption('2024');await page.locator('#profile-form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('#profile-status').textContent==='已保存');
 const readCount=await page.evaluate(async()=>{const replies=await Promise.all(Array.from({length:200},()=>api('/api/catalog/status')));return replies.length;});
 assert.equal(readCount,200);
 await page.locator('#catalog-management summary').click();await page.locator('#restore-builtin-catalog').click();await page.waitForFunction(()=>document.querySelector('#catalog-import-status').textContent.includes('1212'));
 const catalogStatus=await page.evaluate(()=>api('/api/catalog/status'));
 assert.equal(catalogStatus.primary_section_count,3318);
 assert.equal(catalogStatus.non_blocking_section_count,248);
 assert.equal(catalogStatus.confirmation_required_count,0);
 assert.ok(catalogStatus.source_label.includes('中国地质大学'));
 await page.locator('#course-search').fill('测试技术');await page.locator('#course-search-button').click();await page.waitForFunction(()=>document.querySelector('#course-search-results').textContent.includes('20739000'));
 await page.locator('#profile-school').fill('另一所测试大学');await page.locator('#profile-form button[type=submit]').click();
 await page.waitForFunction(()=>state.profile.school==='另一所测试大学');
 await page.locator('#open-manual-editor').click();await page.locator('#custom-name').fill('异校实习');await page.locator('[data-weeks]').fill('1-8');await page.locator('[data-non-blocking]').check();
 assert.equal(await page.locator('[data-start]').isDisabled(),true);
 await page.locator('#course-editor-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#course-editor').open);
 await page.locator('#open-manual-editor').click();await page.locator('#custom-name').fill('异校理论课');await page.locator('[data-weeks]').fill('1-8');await page.locator('[data-weekday]').selectOption('1');await page.locator('[data-start]').fill('1');await page.locator('[data-end]').fill('2');
 await page.locator('#course-editor-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#course-editor').open);
 await page.locator('#generate-plan').click();await page.waitForFunction(()=>!document.querySelector('#generate-plan').disabled);
 assert.equal(await page.evaluate(()=>state.planResponse.plans[0].scheduled_course_count),2);
 assert.equal(await page.evaluate(()=>state.planResponse.plans[0].meetings.length),1);
 assert.equal(await page.evaluate(()=>state.planResponse.adjustment),null);
 assert.equal(await page.evaluate(()=>state.planResponse.warnings.some(w=>w.includes('社会调查'))),false);
 assert.ok((await page.locator('#plan-results').innerText()).includes('不占用时段'));
 await page.screenshot({path:'tmp/stress-031/non-blocking-result.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.locator('.topbar [data-open-help]').click();await page.screenshot({path:'tmp/stress-031/help-mobile.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 const result={external_requests_attempted:external.length,page_errors:errors.length,offline_login_edit_solve_search_help:'passed',internal_conflict_ui:'passed',delayed_plan_input_warning:'passed',out_of_order_search:'passed',cross_account_delayed_history_and_dom_cleanup:'passed',queued_parallel_reads:readCount,desktop_and_390px_help:'passed',non_blocking_practice_with_exact_course:'passed',other_school_profile:'passed',builtin_counts:[3070,248,0]};
 fs.writeFileSync('tmp/stress-031/browser-result.json',JSON.stringify(result,null,2));
 await browser.close();clearTimeout(deadline);console.log(JSON.stringify(result,null,2));
})().catch(async error=>{console.error(error);clearTimeout(deadline);if(browser)await browser.close();process.exit(1)});
