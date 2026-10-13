import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import { overview } from './fixtures/controlPanel.mjs';

const server = await createServer({
  root:fileURLToPath(new URL('..',import.meta.url)),
  server:{middlewareMode:true,watch:null,hmr:false},
  optimizeDeps:{noDiscovery:true,include:[]},
});
after(()=>server.close());
const {default:Overview}=await server.ssrLoadModule('/src/components/staff/StaffOverview.vue');
const {default:Header}=await server.ssrLoadModule('/src/components/Header.vue');
const {changedStaffSettings,chartHeight,staffAccessOptions,staffError,staffTime}=await server.ssrLoadModule('/src/core/staff.ts');
const router=createRouter({history:createMemoryHistory(),routes:[
  ...['builder_world_admin','builder_world_admin_instance','builder_world_player_details','staff_user_info','lobby_building'].map(name=>({path:`/${name}/:world_id?/:player_id?/:instance_id?/:user_id?`,name,component:{}})),
  {path:'/staff',name:'staff_index',component:{}},{path:'/lobby',name:'lobby',component:{}},
]});
await router.push('/staff');
const render=data=>renderToString(createSSRApp(Overview,{data}).use(router));

test('overview shows platform totals, accurate metric definitions, capped rows and world administration links',async()=>{
  const html=await render(overview);
  assert.match(html,/1,248 accounts/);
  assert.match(html,/37 total/);
  assert.match(html,/Showing 3 of 37 online characters/);
  assert.match(html,/World entrants/);
  assert.match(html,/does not measure session duration or all player activity/);
  assert.match(html,/briefly lag disconnection cleanup/);
  assert.match(html,/builder_world_admin\/12/);
  assert.match(html,/builder_world_player_details\/12\/1/);
  assert.match(html,/Daily counts/);
});

test('empty installations render real zero values and empty states without invalid chart heights',async()=>{
  const data=structuredClone(overview);
  data.counts=Object.fromEntries(Object.keys(data.counts).map(key=>[key,0]));
  data.online_worlds=[];data.online_characters=[];data.activity=data.activity.map(day=>({...day,signups:0,active_users:0}));
  const html=await render(data);
  assert.match(html,/No worlds are currently online/);
  assert.match(html,/No characters are currently connected/);
  assert.match(html,/No signups or world entries/);
  assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  assert.equal(chartHeight(0,0),'0%');
  assert.equal(chartHeight(3,6),'50%');
});

test('header exposes platform controls only to staff',async()=>{
  for(const staff of [true,false]){
    const html=await renderToString(createSSRApp(Header).use(router).use(createStore({
      state:{auth:{user:{id:1,is_staff:staff}}},getters:{isAuthenticated:()=>true},
    })));
    assert.equal(html.includes('aria-label="Control Panel"'),staff);
  }
});

test('saving one setting does not overwrite unrelated concurrent administrator changes',()=>{
  const original={access_mode:'invite_only',maintenance_mode:false,building_enabled:false,main_world_id:12};
  const draft={...original,main_world_id:null};
  assert.deepEqual(changedStaffSettings(draft,original),{main_world_id:null});
  assert.deepEqual(changedStaffSettings(original,original),{});
  assert.deepEqual(changedStaffSettings({...original,building_enabled:true},original),{building_enabled:true});
});

test('access controls offer the three database policies without deployment overrides',()=>{
  assert.deepEqual(staffAccessOptions.map(option=>option.value),['open','invite_only','closed']);
  assert.deepEqual(staffAccessOptions.map(option=>option.label),['Open','Invite only','Closed']);
  assert.match(staffAccessOptions.find(option=>option.value==='invite_only').description,/whitelisted emails/);
});

test('API errors preserve validation messages and missing telemetry stays unavailable',()=>{
  assert.equal(staffError({response:{data:{email:['This email is already on the allowlist.']}}},'fallback'),'This email is already on the allowlist.');
  assert.equal(staffError(new Error('offline'),'Retry this request.'),'Retry this request.');
  assert.equal(staffTime(null),'—');
  assert.equal(staffTime('invalid'),'—');
});
