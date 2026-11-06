const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{id,value:'',dataset:{},style:{},attributes:{},handlers:{},textContent:'',innerHTML:'',classList:{toggle(){},add(){},remove(){}},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},addEventListener(k,f){this.handlers[k]=f;},focus(){},append(){},remove(){},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);}
const tabs=['chat','tasks','notes','review'].map(t=>{const n=node('tab-'+t);n.dataset.nav=t;n.attributes.role='tab';return n;});
const panels=tabs.map(t=>node('panel-'+t.dataset.nav));
const document={getElementById:node,createElement:()=>node(Math.random()),addEventListener(){},querySelectorAll(s){return s==='.panel'?panels:s==='[role=tab]'||s==='[data-nav]'?tabs:[];}};
const ctx=vm.createContext({document,Intl,Date,console,history:{replaceState(){}},location:{hash:''},sessionStorage:{getItem(){return null;},setItem(){},removeItem(){}},setInterval(){},setTimeout(){},clearTimeout(){},confirm(){return true;}});
const html=fs.readFileSync('src/enmotion_robot/static/work.html','utf8');
let script=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('init();setInterval(status,4000);','setInterval(status,4000);');
vm.runInContext(script,ctx);
node('chatInput').value='未发送的想法';node('noteBody').value='尚未保存的笔记';node('chatScroll').scrollTop=123;
vm.runInContext("navigate('notes');navigate('tasks');navigate('chat');",ctx);
assert.equal(node('chatInput').value,'未发送的想法');assert.equal(node('noteBody').value,'尚未保存的笔记');assert.equal(node('chatScroll').scrollTop,123);
assert.equal(node('tab-chat').attributes['aria-selected'],'true');assert.equal(node('tab-notes').attributes['aria-selected'],'false');
vm.runInContext("data.tasks=[{id:1,title:'<script>不执行</script>',done:0,due:new Date(Date.now()-1000).toISOString()}];renderTasks();",ctx);
assert.match(node('reminderList').innerHTML,/已到提醒时间/);assert.match(node('reminderList').innerHTML,/&lt;script&gt;/);
assert.match(node('toast').textContent,/到时间了/);
node('toast').textContent='';vm.runInContext('renderReminders();',ctx);assert.equal(node('toast').textContent,'');
console.log('work UI: tab drafts, scroll state, reminders, escaping and deduplication passed');
