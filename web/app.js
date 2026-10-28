"use strict";
const $ = (id) => document.getElementById(id);
const state = {bootstrap:null, session:null, review:null, busy:false, voiceBusy:false, pending:null, evaluationPending:null};
const pages = ["setup","workspace","review-page","history-page"];
const modeLabel = (mode) => mode === "gigachat" ? "Свободный ввод · GigaChat" : "Демонстрация · без нейросети";
const turnNoun = count => ({one:"реплика",few:"реплики",many:"реплик",other:"реплики"})[new Intl.PluralRules("ru").select(count)];
function node(tag, className, text) { const el = document.createElement(tag); if(className) el.className=className; if(text !== undefined) el.textContent=text; return el; }
function showPage(id) { if(id!=="workspace")window.TrainerVoice?.cancel();document.body.classList.toggle("call-page",id==="workspace");pages.forEach(p=>$(p).hidden=p!==id); $("loading").hidden=true; document.querySelectorAll("[data-nav]").forEach(b=>b.classList.toggle("active",b.dataset.nav===(id==="history-page"?"history":"practice"))); if(state.page!==id)window.scrollTo({top:0});state.page=id; }
function setBusy(value) {
  state.busy=value;document.body.classList.toggle("busy",value);
  document.querySelectorAll("button:not(#close-help):not(#close-toast)").forEach(b=>{b.disabled=value;});
  $("send").disabled=value||state.voiceBusy;$("send").textContent=value?"Подождите…":"Отправить ↗";
  $("request-evaluation").disabled=value||!state.bootstrap?.ai_ready;
  $("activity").hidden=!value||state.page!=="workspace";
  $("activity").textContent=state.pending?.kind==="turns"?"Клиент обдумывает ответ…":"Подождите немного…";
  $("review-progress").hidden=!value||!state.evaluationPending;
  window.TrainerVoice?.refresh();
}
function jumpToTurn(turnId) {
  renderSession(state.session);
  requestAnimationFrame(()=>{const item=[...$("transcript").querySelectorAll("[data-turn-id]")].find(el=>el.dataset.turnId===turnId);if(item){item.scrollIntoView({behavior:"smooth",block:"center"});item.classList.add("quote-highlight");item.tabIndex=-1;item.focus({preventScroll:true});setTimeout(()=>item.classList.remove("quote-highlight"),3500);}});
}
function appendEvidence(parent,item) {
  parent.append(node("blockquote","",item.quote));
  const link=node("button","evidence-link",`К реплике ${item.turn_id} ↗`);link.type="button";
  link.addEventListener("click",()=>jumpToTurn(item.turn_id));parent.append(link);
}
function reviewHighlights(report) {
  const area=$("review-highlights");area.replaceChildren();
  const visible=report.criteria.filter(c=>!c.hidden&&c.status!=="not_applicable");
  const good=visible.filter(c=>c.status==="met").slice(0,2);
  const priority=["C04","C05","C07","C10","C02","C03","C01","C06","C08","C09"];
  const improve=visible.filter(c=>c.status==="not_met").sort((a,b)=>priority.indexOf(a.id)-priority.indexOf(b.id)).slice(0,3);
  const render=(title,items,kind)=>{
    if(!items.length)return;
    const section=node("section",`feedback-column ${kind}`);section.append(node("h2","",title));
    items.forEach(c=>{const item=node("article","feedback-item"),body=node("div","feedback-detail");
      item.append(node("h3","",c.title));body.append(node("p","",c.reason));if(c.next_step)body.append(node("p","next-step",c.next_step));
      if(c.evidence?.[0])appendEvidence(body,c.evidence[0]);item.append(disclosure(body,"Подробнее",`Подробнее: ${c.title}`));section.append(item);
    });area.append(section);
  };
  render("Что получилось",good,"strengths");render("Фокус следующей попытки",improve,"improve");area.hidden=!area.children.length;
}
function notify(text, retry=false) { $("toast-text").textContent=text; $("retry").hidden=!retry; $("toast").hidden=false; }
async function api(path, body) {
  const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),path.endsWith('/evaluate')?200000:100000);
  try {
    const response=await fetch(path,{method:body?"POST":"GET",headers:body?{"Content-Type":"application/json","X-Trainer-Token":state.bootstrap.token}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal});
    const data=await response.json();
    if(!response.ok) { const err=new Error(data.error||"Не удалось выполнить действие."); err.session=data.session; err.known=true; throw err; }
    return data;
  } catch(error) { if(!error.known) { const err=new Error("Ответ сервера не получен. Проверьте, что локальный сервер запущен."); err.network=true; throw err; } throw error; }
  finally { clearTimeout(timer); }
}
function renderSession(session) {
  state.session=session;
  $("session-mode").textContent=modeLabel(session.mode);
  $("client-note").textContent=[session.version_notice,session.client_reply_notice].filter(Boolean).join(" ");
  $("client-note").hidden=!$("client-note").textContent;
  $("phase").textContent=session.phase_label;
  $("client-character").textContent=session.character?.label||"Спокойный";
  $("character-note").textContent=session.character?.description||"";
  $("turn-counter").textContent=`${session.turn_count} / ${session.max_turns} реплик`;
  $("meeting-time").textContent=`Сегодня, ${session.appointment.time}`;
  $("meeting-address").textContent=session.appointment.address;
  const log=$("transcript"); log.replaceChildren();
  if(!session.history.length){
    const opening=node("div","call-opening");
    opening.append(node("strong","","Начните разговор"),node("p","","Первая реплика за вами. Поздоровайтесь и обозначьте повод звонка."));
    log.append(opening);
  }
  session.history.forEach(turn=>{
    if(turn.role==="event") {log.append(node("div","event",turn.text));return;}
    const msg=node("div",`message ${turn.role}`);
    msg.dataset.turnId=turn.id;
    msg.append(node("span","message-label",turn.role==="employee"?"Вы":session.client_name),node("div","message-body",turn.text)); log.append(msg);
  });
  const ended=session.status==="ended";
  $("guided-composer").hidden=ended||session.mode!=="guided";
  $("free-composer").hidden=ended||session.mode!=="gigachat";
  $("employee-text").placeholder=session.history.length?"Напишите так, как сказали бы клиенту…":"Начните с приветствия…";
  $("ended-banner").hidden=!ended;
  $("finish").hidden=ended;
  if(ended)window.TrainerVoice?.cancel();
  $("ended-outcome").textContent=session.outcome_label;
  $("choices").replaceChildren();
  session.options.forEach((option,index)=>{
    const button=node("button","choice");button.type="button";button.disabled=state.busy;
    button.append(node("span","choice-number",String(index+1).padStart(2,"0")),node("span","",option.text));
    button.addEventListener("click",()=>sendTurn({option_id:option.id}));$("choices").append(button);
  });
  showPage("workspace");requestAnimationFrame(()=>{log.scrollTop=log.scrollHeight;});
  window.TrainerVoice?.observe(session);
}
async function startSession() {
  if(state.busy)return;
  if(state.pending){notify("Сначала повторите отправку, чтобы уточнить состояние текущего разговора.",true);return;}
  setBusy(true);
  try { const session=await api("/api/sessions",{mode:document.querySelector('input[name="mode"]:checked').value,ending:$("ending").value,character:$("character").value});$("toast").hidden=true;$("employee-text").value="";renderSession(session); }
  catch(error){notify(error.message);}
  finally{setBusy(false);}
}
async function performPending() {
  if(state.busy||!state.pending)return;
  const pending=state.pending;setBusy(true);$("retry").hidden=true;
  try {
    const session=await api(`/api/sessions/${pending.sessionId}/${pending.kind}`,pending.body);
    state.pending=null;$("toast").hidden=true;
    if(pending.kind==="turns")$("employee-text").value="";
    renderSession(session);
  } catch(error) {
    if(error.session)renderSession(error.session);
    if(error.network)notify("Ответ не получен. Повторите ту же отправку — она не добавит реплику дважды.",true);
    else{state.pending=null;notify(error.message);}
  } finally{setBusy(false);}
}
function sendTurn(fields,kind="turns") {
  if(state.busy||state.voiceBusy||!state.session)return;
  if(state.pending){notify("Предыдущая отправка ещё не подтверждена. Повторите её.",true);return;}
  state.pending={kind,sessionId:state.session.id,body:{request_id:crypto.randomUUID(),revision:state.session.revision,...fields}};
  performPending();
}
async function openReview() {
  if(state.busy||!state.session)return;
  setBusy(true);
  try {
    renderReview(await api(`/api/sessions/${state.session.id}/review`));
  }catch(error){notify(error.message);}finally{setBusy(false);}
}
function renderReview(report) {
  state.review=report;
  const assessed=report.evaluation_status==="completed";
  $("review-page").classList.toggle("is-assessed",assessed);
  $("review-notice-text").textContent=assessed?report.notice:(report.evaluation_error||"Проверьте разговор, чтобы увидеть результат по каждому критерию и сверку условий карты.");
  $("request-evaluation").hidden=assessed;$("evaluation-cost-note").hidden=assessed;
  $("request-evaluation").textContent=state.evaluationPending?"Проверить результат отправки":"Получить оценку GigaChat";
  $("review-verdict").hidden=!assessed;$("review-verdict").replaceChildren();
  $("review-rating").hidden=!assessed||!report.rating;$("review-rating").replaceChildren();
  $("review-highlights").hidden=!assessed;
  if(assessed){
    if(report.rating){
      const r=report.rating,score=node("div","rating-score");score.append(node("strong","",r.value===null?"—":String(r.value)),node("span","","/ 100"));
      const detail=node("div","rating-detail");
      detail.append(node("span","eyebrow","КАЧЕСТВО РАЗГОВОРА"),node("h2","",r.provisional?"Предварительный результат":r.value===100?"Условия упражнения выполнены":"Результат тренировки"),node("p","",`Выполнено ${r.met} из ${r.applicable} критериев${r.not_met?` · Доработать: ${r.not_met}`:""}`));
      const track=node("div","rating-track"),fill=node("div","rating-fill");track.setAttribute("role","progressbar");track.setAttribute("aria-label","Доля выполненных критериев");track.setAttribute("aria-valuemin","0");track.setAttribute("aria-valuemax","100");
      if(r.value!==null){track.setAttribute("aria-valuenow",String(r.value));fill.style.width=`${r.value}%`;}
      track.append(fill);detail.append(track);
      const method=node("p","rating-method","Каждый применимый критерий имеет равный вес. Шаги, для которых не возникло ситуации, исключены из расчёта.");
      detail.append(disclosure(method,"Как считается рейтинг","Как считается рейтинг"));$("review-rating").append(score,detail);
    }
    const labels={passed:"Критерии выполнены",retry:"Что улучшить в следующем звонке",requires_review:"Разбор не завершён"};
    $("review-verdict").append(node("h2","",labels[report.training_verdict]||"Разбор не завершён"));
    (report.evaluation_warnings||[]).forEach(text=>$("review-verdict").append(node("p","small",text)));
    if(report.coverage_note)$("review-verdict").append(node("p","small",report.coverage_note));
    $("review-verdict").hidden=report.training_verdict!=="requires_review"&&!(report.evaluation_warnings||[]).length&&!report.coverage_note;
    reviewHighlights(report);
  }
  $("review-outcome").textContent=report.outcome_label;$("review-turns").textContent=report.turn_count;
  $("review-turns-label").textContent=`${turnNoun(report.turn_count)} сотрудника`;
  renderCriteria(report,assessed);renderClaims(report,assessed);
  showPage("review-page");window.scrollTo({top:0});
}
async function requestEvaluation() {
  if(state.busy||!state.session||state.review?.evaluation_status==="completed")return;
  if(state.evaluationPending&&state.evaluationPending.sessionId!==state.session.id){notify("Откройте предыдущий разбор, чтобы уточнить результат отправленной оценки.");return;}
  if(!state.evaluationPending)state.evaluationPending={sessionId:state.session.id,body:{request_id:crypto.randomUUID(),revision:state.session.revision}};
  const pending=state.evaluationPending;setBusy(true);$("request-evaluation").textContent="Проверяем разговор…";
  try{
    const result=await api(`/api/sessions/${pending.sessionId}/evaluate`,pending.body);
    state.session=result.session;state.evaluationPending=null;renderReview(result.review);
  }catch(error){
    if(error.session)state.session=error.session;
    if(error.known)state.evaluationPending=null;
    notify(error.network?"Ответ оценки пока не получен. Нажмите «Проверить результат отправки»: повторная проверка не будет списана за тот же запрос.":error.message);
    if(error.known){try{renderReview(await api(`/api/sessions/${state.session.id}/review`));}catch{}}
    $("request-evaluation").textContent=state.evaluationPending?"Проверить результат отправки":"Повторить автооценку";
  }finally{setBusy(false);}
}
async function showHistory() {
  if(state.busy)return;setBusy(true);
  try {
    const sessions=await api("/api/sessions");$("history-list").replaceChildren();
    if(!sessions.length)$("history-list").append(node("div","empty","Здесь появятся ваши разговоры. Начните с первого упражнения."));
    sessions.forEach(session=>{
      const row=node("button","history-row"); const body=node("div");
      const date=new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",day:"numeric",month:"long",hour:"2-digit",minute:"2-digit"}).format(new Date(session.created_at));
      body.append(node("strong","",session.outcome_label),node("small","",`${date} · ${session.character?.label||"Спокойный"} · ${modeLabel(session.mode)} · ${session.turn_count} ${turnNoun(session.turn_count)}`));
      row.append(body);
      if(session.rating){const r=session.rating;const badge=node("div",`history-rating${r.provisional?" provisional":""}`);badge.append(node("strong","",r.value===null?"—":`${r.value}/100`),node("small","",r.provisional?"предварительно":"рейтинг"));row.append(badge);}
      row.append(node("span","",session.status==="ended"?"Разбор →":"Продолжить →"));
      row.addEventListener("click",()=>loadSession(session.id));$("history-list").append(row);
    });showPage("history-page");
  }catch(error){notify(error.message);}finally{setBusy(false);}
}
async function loadSession(id) {
  if(state.busy)return;
  if(state.pending&&state.pending.sessionId!==id){notify("Сначала уточните состояние предыдущей отправки.",true);return;}
  setBusy(true);
  try{renderSession(await api(`/api/sessions/${id}`));}catch(error){notify(error.message);}finally{setBusy(false);}
  if(state.session?.id===id&&state.session.status==="ended")await openReview();
}
function practice() {if(state.busy)return; if(state.session?.status==="active")renderSession(state.session);else showPage("setup");}
function downloadReview() {
  if(!state.review)return;
  const blob=new Blob([JSON.stringify(state.review,null,2)],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download=`do-vstrechi-${state.review.session_id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
document.querySelectorAll("[data-nav]").forEach(button=>button.addEventListener("click",()=>{
  if(button.dataset.nav==="practice")practice();else if(button.dataset.nav==="history")showHistory();else $("help").showModal();
}));
$("start").addEventListener("click",startSession);
$("finish").addEventListener("click",()=>sendTurn({},"finish"));
$("retry").addEventListener("click",performPending);
$("close-toast").addEventListener("click",()=>$("toast").hidden=true);
$("close-help").addEventListener("click",()=>$("help").close());
$("setup-help").addEventListener("click",()=>$("help").showModal());
$("open-review").addEventListener("click",openReview);
$("new-attempt").addEventListener("click",()=>showPage("setup"));
$("history-new").addEventListener("click",()=>showPage("setup"));
$("back-to-call").addEventListener("click",()=>renderSession(state.session));
$("download-review").addEventListener("click",downloadReview);
$("request-evaluation").addEventListener("click",requestEvaluation);
$("free-composer").addEventListener("submit",event=>{event.preventDefault();const text=$("employee-text").value.trim();if(text)sendTurn({text});});
$("employee-text").addEventListener("keydown",event=>{if(event.key==="Enter"&&event.ctrlKey){event.preventDefault();$("free-composer").requestSubmit();}});
(async()=>{
  try {
    state.bootstrap=await api("/api/bootstrap");
    (state.bootstrap.characters||[]).forEach(p=>{const option=node("option","",p.label);option.value=p.id;$("character").append(option);});
    const describeCharacter=()=>{$("character-description").textContent=(state.bootstrap.characters||[]).find(p=>p.id===$("character").value)?.description||"";};
    $("character").addEventListener("change",describeCharacter);describeCharacter();
    window.TrainerVoice?.init({ready:state.bootstrap.voice?.ready,token:state.bootstrap.token,onError:notify,canRecord:()=>!state.busy&&state.session?.status==="active",onState:active=>{state.voiceBusy=active;$("send").disabled=state.busy||active;$("finish").disabled=state.busy||active;}});
    if(!state.bootstrap.ai_ready){$("ai-mode").disabled=true;$("ai-card").classList.add("unavailable");$("ai-description").textContent="Станет доступно после настройки ключа GigaChat.";$("ai-setup-hint").hidden=false;}
    $("start").disabled=false;showPage("setup");
  }catch(error){$("loading").textContent="Учебное пространство недоступно. Запустите сервер через start.cmd и обновите страницу.";notify(error.message);}
})();
