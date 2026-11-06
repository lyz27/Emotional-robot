// Exercise real dashboard playback handlers with a minimal DOM, without audio hardware.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', checked: false, style: {setProperty(){}}, dataset:{}, handlers:{},
    className: '', classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    addEventListener(name, fn){this.handlers[name]=fn;},
    querySelector(){return null;}, querySelectorAll(){return [];}, appendChild(){}, pause(){}, play(){return Promise.resolve();},
  });
  return elements.get(id);
}
const document = {getElementById:element, querySelectorAll:()=>[],
  documentElement:element('root'), createElement:()=>element('temp')};
const context = vm.createContext({document, performance:{now:()=>100},
  setTimeout(){},setInterval(){},console,fetch(){throw Error('unexpected request');}});
let script = fs.readFileSync('src/enmotion_robot/static/index.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
script = script.replace('refresh(); loadMemories(); setInterval(refresh,1000); scheduleBlink();','');
vm.runInContext(script,context);
const sim = {emotion:'worried',gaze:{x:0,y:0},neck:{yaw:0,pitch:0},
  sensors:{touch:false,distance_cm:100,face_detected:false,face_x:0,face_y:0},
  motor:'disabled',blink_count:0,sequence:1,events:[]};
context.sim=sim;
vm.runInContext('pendingSpeech=true;renderSimulation(sim);',context);
assert.match(element('face').className,/calm/);
let appreciationAnimations=0;
element('face').animate=()=>{appreciationAnimations++;};
const reply={action:{emotion:'happy',gaze:{x:0,y:0},neck:{yaw:0,pitch:-4},gesture:'appreciation'},emotion:{intensity:.6}};
context.reply=reply;
vm.runInContext('showSpeechResult({text:"恭喜",audio_url:"/audio/test.wav",companion:reply});',context);
assert.equal(vm.runInContext('presentation',context),null);
element('replyAudio').handlers.playing();
vm.runInContext('renderSimulation(sim);',context);
assert.match(element('face').className,/happy/); // polling cannot replace the speaking expression
assert.equal(element('stage').dataset.emotion,'happy'); // atmosphere follows the displayed reply, not stale server mood
assert.equal(appreciationAnimations,1); // one gesture at playback start, not every polling refresh
element('replyAudio').handlers.pause();
assert.equal(vm.runInContext('presentation',context),null);
console.log('playback expression timing: passed');
