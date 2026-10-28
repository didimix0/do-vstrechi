// Изолированные проверки жизненного цикла записи; настоящий микрофон не включается.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {speechWav}=require('../web/voice.js');
assert.equal(speechWav([new Float32Array(48000*46)],48000).byteLength,44+16000*45*2);
class Element {
  constructor(){this.textContent='';this.value='';this.checked=false;this.events={};this.classList={add(){},remove(){}};}
  addEventListener(name,fn){this.events[name]=fn;}focus(){}
  click(){return this.events.click();}
}
function environment({deny=false}={}){
  const elements={};const el=id=>elements[id]??=new Element();let stopped=0,worklet,utterances=[],errors=[],active=false,fetches=0;
  class AudioContext {
    constructor(){this.sampleRate=48000;this.audioWorklet={addModule:async()=>{}};}
    createMediaStreamSource(){return {connect(){}};}createGain(){return {gain:{value:1},connect(){},disconnect(){}};}async close(){}
  }
  class Worklet {
    constructor(){this.port={};worklet=this;}connect(){}disconnect(){}
  }
  const document={getElementById:el,addEventListener(){}};
  const sandbox={document,navigator:{mediaDevices:{getUserMedia:async()=>{
    if(deny)throw Object.assign(new Error(),{name:'NotAllowedError'});
    return {getTracks:()=>[{stop(){stopped++;}}]};
  }}},AudioContext,AudioWorkletNode:Worklet,AbortController,Float32Array,DataView,ArrayBuffer,Date,
  setTimeout,clearTimeout,setInterval,clearInterval,SpeechSynthesisUtterance:class {constructor(text){this.text=text;}},
  speechSynthesis:{getVoices:()=>[{lang:'ru-RU',localService:true}],cancel(){},speak(u){utterances.push(u.text);},addEventListener(){}},
  fetch:async(url,options)=>{fetches++;assert.equal(url,'/api/voice/transcribe');assert.equal(options.headers['X-Trainer-Token'],'local-token');assert(options.body.byteLength>32000);return {ok:true,json:async()=>({text:'здравствуйте я представитель втб'})};},
  window:{AudioWorkletNode:Worklet,addEventListener(){}}};
  sandbox.window.speechSynthesis=sandbox.speechSynthesis;
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync('web/voice.js','utf8'),sandbox);
  const voice=sandbox.window.TrainerVoice;
  voice.init({ready:true,token:'local-token',canRecord:()=>true,onError:e=>errors.push(e),onState:v=>active=v});
  return {el,voice,errors,utterances,get active(){return active;},get stopped(){return stopped;},get fetches(){return fetches;},samples(){worklet.port.onmessage({data:new Float32Array(48000).fill(.2)});}};
}
(async()=>{
  const e=environment();e.el('employee-text').value='Начало.';
  await e.el('voice-record').click();assert(e.active);e.samples();
  await e.el('voice-record').click();assert(!e.active);assert.equal(e.stopped,1);assert.equal(e.fetches,1);
  assert.equal(e.el('employee-text').value,'Начало. здравствуйте я представитель втб');
  assert.equal(e.errors.length,0);assert(e.el('voice-status').textContent.includes('Проверьте'));
  // Произнесённая реплика остаётся в поле; отправки хода здесь нет.
  assert.equal(e.fetches,1);
  e.el('speak-client').checked=true;e.voice.observe({id:'a',history:[]});
  const first={id:'c1',role:'client',text:'Здравствуйте.'};e.voice.observe({id:'a',history:[first]});
  e.voice.observe({id:'a',history:[first]});assert.deepEqual(e.utterances,['Здравствуйте.']);
  const denied=environment({deny:true});await denied.el('voice-record').click();assert(!denied.active);assert.equal(denied.fetches,0);assert(denied.errors[0].includes('не разрешён'));
  const canceled=environment();await canceled.el('voice-record').click();canceled.samples();canceled.voice.cancel();assert.equal(canceled.stopped,1);assert.equal(canceled.fetches,0);assert(!canceled.active);
  console.log('Voice UI: editable transcript, cleanup, denied microphone, cancellation and playback passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
