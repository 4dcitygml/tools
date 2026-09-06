// Copyright (c) 2026 4dcitygml
// SPDX-License-Identifier: Apache-2.0
// Run: node tests/review_filters.test.cjs
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../tools/hub/review.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(x=>x[1]).join('\n');
new vm.Script(script);
const functions = script.slice(script.indexOf('function approvalFilterKey'),script.indexOf('async function loadQueue'));
vm.runInNewContext(`
  const elements={approvalFilter:{},approvalUnknown:{}};
  const $=id=>elements[id];
  const esc=String;
  const t=(key,fallback,args)=>fallback;
  const saved=new Map();
  const localStorage={getItem:key=>saved.get(key),setItem:(key,v)=>saved.set(key,v)};
  let lastLoad;
  const loadQueue=(...args)=>lastLoad=args;
  let queueData={nwo:'city/repo',login:'person-a',approvalPolicy:{required:4},items:[]};
  ${functions}
  const assert=(test,message)=>{if(!test)throw Error(message);};
  const ready=n=>({number:n,queueStatus:'reviewer_waiting',approvals:{known:true,remaining:n,approved:4-n,required:4}});
  const items=[ready(1),ready(2),ready(3),{number:10,queueStatus:'checking',approvals:{known:true,remaining:2}},
    {number:11,queueStatus:'reviewer_waiting',approvals:{known:false,remaining:null}}];
  assert(filterApprovalItems(items,'2').map(x=>x.number).join()==='2','remaining 2 excludes other counts and unfinished CI');
  assert(filterApprovalItems(items,'1').length===1,'remaining 1');
  assert(filterApprovalItems(items,'unknown')[0].number===11,'unknown is not zero');
  assert(filterApprovalItems(items,'all').length===5,'all restores hidden PRs');
  assert(validApprovalFilter('-1')==='all'&&validApprovalFilter('01')==='all','invalid saved values');
  setupApprovalFilter(); elements.approvalFilter.value='2';elements.approvalFilter.onchange();
  assert(lastLoad[1]==='2','selection updates view');
  assert(setupApprovalFilter()==='2','reload restores setting');
  queueData.login='person-b';assert(setupApprovalFilter()==='all','accounts isolated');
  queueData.login='person-a';queueData.nwo='city/other';assert(setupApprovalFilter()==='all','repositories isolated');
  queueData.nwo='city/repo';queueData.approvalPolicy.required=1;
  assert(setupApprovalFilter()==='2','policy change preserves personal choice');
  assert(filterApprovalItems([ready(1)],'2').length===0,'changed count can make the filter empty');
  localStorage.setItem=()=>{throw Error('Storage disabled');};
  elements.approvalFilter.value='1';elements.approvalFilter.onchange();
  assert(lastLoad[1]==='1','storage failure must not prevent filtering');
`);
console.log('Review script syntax and filter/preference regression scenarios passed');
