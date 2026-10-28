"use strict";
// WAV остаётся в памяти вкладки; текст отправляется в диалог только пользователем.
function speechWav(chunks, sourceRate) {
  const total=chunks.reduce((sum,c)=>sum+c.length,0);
  const input=new Float32Array(total);let at=0;
  chunks.forEach(c=>{input.set(c,at);at+=c.length;});
  const count=Math.min(45*16000,Math.floor(total*16000/sourceRate));
  const buffer=new ArrayBuffer(44+count*2), view=new DataView(buffer);
  const label=(offset,text)=>{[...text].forEach((ch,i)=>view.setUint8(offset+i,ch.charCodeAt(0)));};
  label(0,'RIFF');view.setUint32(4,36+count*2,true);label(8,'WAVE');label(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
  label(36,'data');view.setUint32(40,count*2,true);
  for(let i=0;i<count;i++) {
    const start=Math.floor(i*sourceRate/16000),end=Math.max(start+1,Math.floor((i+1)*sourceRate/16000));
    let sum=0;for(let j=start;j<Math.min(end,total);j++)sum+=input[j];
    const value=Math.max(-1,Math.min(1,sum/(Math.min(end,total)-start)));
    view.setInt16(44+i*2,Math.round(value*(value<0?32768:32767)),true);
  }
  return buffer;
}
if(typeof module!=="undefined")module.exports={speechWav};
if(typeof window!=="undefined")window.TrainerVoice=(()=>{
  let options,stream,context,processor,sink,chunks=[],timer,started=0,recording=false,working=false,ticket=0,sessionId=null;
  let lastSeen=null,lastReply=null,requestController=null;
  const $=id=>document.getElementById(id);
  const setStatus=text=>{$('voice-status').textContent=text;};
  const ruVoice=()=>('speechSynthesis' in window?speechSynthesis.getVoices():[]).find(v=>/^ru([-_]|$)/i.test(v.lang)&&v.localService);
  function updateVoices() {
    const available=!!ruVoice();
    $('speak-client').disabled=!available;
    $('speak-hint').textContent=available?'Голос устройства':'На устройстве нет русского голоса';
    $('replay-client').hidden=!lastReply||!available;
  }
  function refresh() {
    if(!options)return;
    $('voice-record').disabled=working||!options.ready||(!recording&&!options.canRecord());
    updateVoices();
  }
  function speak(text) {
    const voice=ruVoice();if(!voice||recording||working)return;
    speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(text);
    utterance.lang='ru-RU';utterance.voice=voice;utterance.rate=1;utterance.pitch=1;
    utterance.onerror=event=>{if(!['canceled','interrupted'].includes(event.error))options.onError('Не удалось озвучить ответ. Его можно прочитать в разговоре.');};
    speechSynthesis.speak(utterance);
  }
  function release() {
    clearInterval(timer);timer=null;
    if(processor){processor.port.onmessage=null;processor.disconnect();processor=null;}
    if(sink){sink.disconnect();sink=null;}
    if(stream){stream.getTracks().forEach(track=>track.stop());stream=null;}
    const previous=context;context=null;if(previous)previous.close().catch(()=>{});
  }
  function cancel() {
    ticket++;recording=false;working=false;chunks=[];release();requestController?.abort();requestController=null;
    if('speechSynthesis' in window)speechSynthesis.cancel();
    if(options){options.onState(false);$('voice-record').classList.remove('recording');$('voice-record').textContent='Записать реплику';refresh();setStatus(options.ready?'До 45 секунд. Проверьте текст перед отправкой.':options.unavailableHint);}
  }
  async function start() {
    if(working||!options.ready||!options.canRecord())return;
    const current=++ticket;recording=true;chunks=[];options.onState(true);
    if('speechSynthesis' in window)speechSynthesis.cancel();
    $('voice-record').textContent='Отменить подключение';setStatus('Разрешите доступ к микрофону…');
    try {
      const acquired=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if(current!==ticket||!recording){acquired.getTracks().forEach(track=>track.stop());return;}
      stream=acquired;const captureContext=new AudioContext();context=captureContext;await captureContext.audioWorklet.addModule('/audio-input.js');
      if(current!==ticket||!recording)return;
      const source=context.createMediaStreamSource(stream);processor=new AudioWorkletNode(context,'speech-capture');
      sink=context.createGain();sink.gain.value=0;source.connect(processor);processor.connect(sink);sink.connect(context.destination);
      const rate=context.sampleRate;let samples=0;
      processor.port.onmessage=event=>{if(recording){chunks.push(event.data);samples+=event.data.length;if(samples>=rate*45)stop();}};
      started=Date.now();$('voice-record').textContent='Остановить запись';$('voice-record').classList.add('recording');
      setStatus('Идёт запись · 0 / 45 с');timer=setInterval(()=>setStatus(`Идёт запись · ${Math.min(45,Math.floor((Date.now()-started)/1000))} / 45 с`),1000);
    }catch(error){
      if(current!==ticket)return;
      cancel();options.onError(error.name==='NotAllowedError'?'Доступ к микрофону не разрешён. Можно продолжить текстом.':'Микрофон недоступен. Проверьте подключение или продолжите текстом.');
    }
  }
  async function stop() {
    if(!recording)return;
    if(!context){cancel();return;}
    const current=ticket,rate=context.sampleRate,parts=chunks;chunks=[];recording=false;working=true;release();
    $('voice-record').classList.remove('recording');$('voice-record').disabled=true;setStatus('Vosk распознаёт реплику…');
    requestController=new AbortController();const timeout=setTimeout(()=>requestController?.abort(),90000);
    try {
      const wav=speechWav(parts,rate);
      const response=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav','X-Trainer-Token':options.token},body:wav,signal:requestController.signal});
      const result=await response.json();if(current!==ticket)return;
      if(!response.ok)throw new Error(result.error||'Не удалось распознать запись.');
      const area=$('employee-text');area.value=[area.value.trim(),result.text].filter(Boolean).join(' ').slice(0,4000);
      area.focus();setStatus('Текст готов. Проверьте числа и названия, затем отправьте.');
    }catch(error){if(current===ticket){setStatus('Запись не распознана. Можно повторить или написать текст.');options.onError(error.name==='AbortError'?'Распознавание заняло слишком много времени. Попробуйте ещё раз.':error.message);}}
    finally{clearTimeout(timeout);if(current===ticket){working=false;requestController=null;options.onState(false);$('voice-record').disabled=false;$('voice-record').textContent='Записать реплику';}}
  }
  function observe(session) {
    if(sessionId!==session.id){cancel();sessionId=session.id;lastSeen=null;lastReply=null;}
    const last=session.history.at(-1);const key=last?`${session.id}:${last.id}`:null;
    if(last?.role==='client'){
      lastReply=last.text;
      if(lastSeen&&key!==lastSeen&&$('speak-client').checked)speak(last.text);
    }
    lastSeen=key||'empty';updateVoices();
  }
  function init(config) {
    options=config;options.ready=!!config.ready&&!!navigator.mediaDevices?.getUserMedia&&!!window.AudioWorkletNode;
    options.unavailableHint=config.ready?'В этом браузере запись недоступна. Можно написать текст.':'Vosk пока не настроен. Пишите реплику вручную.';
    $('voice-record').disabled=!options.ready;$('voice-toolbar').hidden=false;
    $('voice-record').addEventListener('click',()=>recording?stop():start());
    $('replay-client').addEventListener('click',()=>lastReply&&speak(lastReply));
    $('speak-client').addEventListener('change',()=>{if(!$('speak-client').checked&&'speechSynthesis' in window)speechSynthesis.cancel();});
    if('speechSynthesis' in window)speechSynthesis.addEventListener('voiceschanged',updateVoices);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
    window.addEventListener('pagehide',cancel);cancel();updateVoices();
  }
  return {init,observe,cancel,refresh};
})();
