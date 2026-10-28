import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, symlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { prepareWorldGuides, readGuide, existingSidebar } from './world-guides.mjs';

function fixture(t) {
  const root=mkdtempSync(join(tmpdir(),'world-guides-')); t.after(()=>rmSync(root,{recursive:true,force:true}));
  const docs=join(root,'docs'), pack=join(root,'pack');
  mkdirSync(join(docs,'guides'),{recursive:true});mkdirSync(join(pack,'guide'),{recursive:true});
  writeFileSync(join(docs,'guides/index.md'),'# Core');
  writeFileSync(join(pack,'guide/index.md'),'# Example');
  writeFileSync(join(pack,'editorial.md'),'PRIVATE');
  const manifest={schemaVersion:1,slug:'example',title:'Example',description:'An example world.',files:['index.md'],sidebar:[{text:'Example',link:''}]};
  const path=join(pack,'guide.json');
  const save=()=>writeFileSync(path,JSON.stringify(manifest));save();
  return {docs,pack,manifest,path,save};
}

test('ordinary docs need no world files or private workspace',t=>{
  const f=fixture(t);assert.deepEqual(prepareWorldGuides(f.docs,''),{srcDir:'guides',nav:[],sidebar:{},extended:false});
  assert.equal(existsSync(join(f.docs,'.vitepress/assembled')),false);
});
test('only listed files are composed; worlds get independent routes/navigation',t=>{
  const f=fixture(t);const result=prepareWorldGuides(f.docs,JSON.stringify([f.path]));
  const assembled=join(f.docs,result.srcDir);
  assert.equal(readFileSync(join(assembled,'index.md'),'utf8'),'# Core');
  assert.equal(readFileSync(join(assembled,'worlds/example/index.md'),'utf8'),'# Example');
  assert.equal(existsSync(join(assembled,'worlds/example/editorial.md')),false);
  assert.equal(result.sidebar['/worlds/example/'][0].link,'/worlds/example/');
  f.manifest.files.push('old.md');writeFileSync(join(f.pack,'guide/old.md'),'old');f.save();
  prepareWorldGuides(f.docs,JSON.stringify([f.path]));
  f.manifest.files.pop();f.save();prepareWorldGuides(f.docs,JSON.stringify([f.path]));
  assert.equal(existsSync(join(assembled,'worlds/example/old.md')),false);
});
test('rejects traversal, private includes, and symlinked publication inputs',t=>{
  const f=fixture(t);
  f.manifest.files.push('../editorial.md');f.save();assert.throws(()=>readGuide(f.path),/Unsafe/);
  f.manifest.files.pop();f.save();writeFileSync(join(f.pack,'guide/index.md'),'<!--@include: ../../editorial.md-->');
  assert.throws(()=>readGuide(f.path),/not published/);
  writeFileSync(join(f.pack,'guide/index.md'),'# Example');
  symlinkSync(join(f.pack,'editorial.md'),join(f.pack,'guide/private.md'));
  f.manifest.files.push('private.md');f.save();assert.throws(()=>readGuide(f.path),/symlink/);
});
test('rejects duplicate slugs, routes, invalid navigation, and non-content files',t=>{
  const f=fixture(t);assert.throws(()=>prepareWorldGuides(f.docs,JSON.stringify([f.path,f.path])),/Duplicate/);
  f.manifest.sidebar[0].link='missing';f.save();assert.throws(()=>readGuide(f.path),/not published/);
  f.manifest.sidebar[0].link='';f.manifest.files.push('run.js');f.save();assert.throws(()=>readGuide(f.path),/Unsupported/);
  f.manifest.files=['index.md','a.md','a/index.md'];mkdirSync(join(f.pack,'guide/a'));
  writeFileSync(join(f.pack,'guide/a.md'),'# A');writeFileSync(join(f.pack,'guide/a/index.md'),'# A');f.save();
  // a and a/ are distinct clean URLs, so don't treat those as identical routes.
  f.manifest.files.push('a.md');f.save();assert.throws(()=>readGuide(f.path),/unique/);
});

test('newer renderer omits standard links absent from the selected manual',t=>{
  const f=fixture(t);
  const items=[{text:'Home',link:'/'},{text:'Future section',items:[{text:'New feature',link:'/future'}]}];
  assert.deepEqual(existingSidebar(items,join(f.docs,'guides')),[{text:'Home',link:'/'}]);
  assert.equal(items.length,2);
});
