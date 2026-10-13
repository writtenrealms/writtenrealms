// Real control-panel components with synthetic data. No backend request or write
// can leave this page. Add ?test=1 to exercise draft preservation and failure UX.
import { createApp, h, nextTick } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { createStore } from 'vuex';
import { setLucideProps } from '@lucide/vue';
import axios from 'axios';
import Header from '../../src/components/Header.vue';
import StaffFrame from '../../src/components/staff/StaffFrame.vue';
import Staff from '../../src/views/staff/Staff.vue';
import { overview, settings, allowlist, jobs, audit } from '../fixtures/controlPanel.mjs';
import '../../src/styles/app.scss';

const sampleSettings={...settings};
let settingsFailure=false;
let writes=0;
axios.defaults.adapter=async config=>{
  let data:unknown;
  if(config.method==='get') {
    const responses:Record<string,unknown>={'/staff/overview/':overview,'/staff/site/':sampleSettings,'/staff/allowlist/':allowlist,'/staff/jobs/':jobs,'/staff/audit/':audit,'/lobby/config/':{main_world_id:12,building_enabled:false}};
    if(!(config.url! in responses))throw new Error(`Unexpected preview request: ${config.url}`);
    data=responses[config.url!];
  } else if(config.method==='patch' && config.url==='/staff/site/') {
    writes++;
    if(settingsFailure)throw {response:{data:{detail:'Simulated save failure. Your draft is preserved.'}}};
    Object.assign(sampleSettings,JSON.parse(config.data));data=sampleSettings;
  } else if(config.method==='post' && config.url==='/staff/jobs/') { writes++;data={action:JSON.parse(config.data).action,task_id:'preview-task-id'}; }
  else throw new Error('Preview mutations are limited to local test settings and jobs.');
  return {config,status:200,statusText:'OK',headers:{},data:structuredClone(data)};
};
const routes=[{path:'/',component:StaffFrame,children:[{path:'',name:'staff_index',component:Staff}]},...['builder_world_admin','builder_world_admin_instance','builder_world_player_details','staff_user_info','staff_worlds','staff_signups','lobby','lobby_building'].map(name=>({path:`/${name}/:world_id?/:player_id?/:instance_id?/:user_id?`,name,component:{template:'<p>Preview destination</p>'}}))];
const router=createRouter({history:createMemoryHistory(),routes});
const store=createStore({state:{auth:{user:{id:1,is_staff:true,email:'admin@example.com'}},ui:{notification:null,modal:{isOpen:false}}},getters:{isAuthenticated:()=>true}});
setLucideProps({size:18,strokeWidth:1.75});
await router.push('/');await router.isReady();
createApp({render:()=>h('div',[h(Header),h('div',{id:'main'},[h(RouterView)])])}).use(router).use(store).mount('#app');
const frame=async()=>{await nextTick();await new Promise(requestAnimationFrame);await nextTick();};
function button(text:string){const item=Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node=>node.textContent?.trim()===text);if(!item)throw new Error(`Missing ${text} button`);return item;}
let checks=0;
function check(condition:boolean,message:string){if(!condition)throw new Error(message);checks++;}
async function run(){
  await frame();await frame();
  check(document.body.textContent!.includes('Core Control Panel'),'Panel did not load');
  check(document.querySelectorAll('.metric-card').length===4,'Missing overview metrics');
  check(document.documentElement.scrollWidth<=innerWidth,'Horizontal page overflow');
  button('Access').click();await frame();await frame();
  const badges=[document.querySelector('.control-status .tag')!,document.querySelector('.access-options')!.closest('section')!.querySelector('.tag')!];
  check(badges.every(badge=>badge.textContent==='Invite only'),'Database access policy missing from badges');
  check(document.querySelector<HTMLInputElement>('input[name="access-mode"][value="invite_only"]')!.checked,'Invite only radio does not match saved policy');
  check(document.body.textContent!.includes('alex@example.com') && document.body.textContent!.includes('river@example.com'),'Saved invitations missing');
  check(!document.body.textContent!.includes('deployment'),'Obsolete deployment controls still displayed');
  document.querySelector<HTMLInputElement>('input[name="access-mode"][value="closed"]')!.click();await frame();
  check(badges.every(badge=>badge.textContent==='Invite only'),'Unsaved draft changed live access status');
  button('DISCARD CHANGES').click();await frame();
  const world=document.querySelector<HTMLInputElement>('#main-world-id')!;
  world.value='24';world.dispatchEvent(new Event('input',{bubbles:true}));await frame();
  check(document.body.textContent!.includes('Unsaved changes'),'Changed setting is not marked unsaved');
  button('Overview').click();await frame();button('Access').click();await frame();
  check(document.querySelector<HTMLInputElement>('#main-world-id')!.value==='24','Changing tabs discarded settings draft');
  settingsFailure=true;button('SAVE SETTINGS').click();await frame();await frame();
  check(document.body.textContent!.includes('Simulated save failure'),'Save failure was hidden');
  check(document.querySelector<HTMLInputElement>('#main-world-id')!.value==='24','Failed save discarded settings draft');
  settingsFailure=false;button('SAVE SETTINGS').click();await frame();await frame();
  check(document.body.textContent!.includes('Settings saved.'),'Successful save not confirmed');
  check(button('SAVE SETTINGS').disabled,'Successful save left form dirty');
  check(badges.every(badge=>badge.textContent==='Invite only'),'Unrelated save changed database access status');
  button('Queues & jobs').click();await frame();await frame();
  check(document.body.textContent!.includes('Unavailable'),'Unknown queue values rendered as zero');
  check(document.body.textContent!.includes('configured') || document.body.textContent!.includes('Configured'),'Schedule caveat missing');
  button('Admin history').click();await frame();await frame();
  check(document.body.textContent!.includes('admin@example.com'),'Audit history missing');
  button('Overview').click();await frame();
  check(writes===2,'Preview caused unexpected writes');
  document.querySelector('#results')!.textContent=`PASS: ${checks} control-panel checks at ${innerWidth} × ${innerHeight}`;
}
if(new URLSearchParams(location.search).has('test'))void run().catch(error=>{document.querySelector('#results')!.textContent=`FAIL: ${error.message}`;console.error(error);});
