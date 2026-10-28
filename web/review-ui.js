"use strict";
let disclosureNumber=0;
const shortText=(value,limit=140)=>{const text=(value||"").trim();return text.length>limit?text.slice(0,limit).replace(/\s+\S*$/,"")+"…":text;};
function disclosure(content,label="Подробнее",accessibleLabel=label) {
  const wrap=node("div","disclosure"),button=node("button","more-button",label);
  button.type="button";content.id=`review-detail-${++disclosureNumber}`;content.hidden=true;
  button.setAttribute("aria-expanded","false");button.setAttribute("aria-controls",content.id);button.setAttribute("aria-label",accessibleLabel);
  button.addEventListener("click",()=>{const open=content.hidden;content.hidden=!open;button.setAttribute("aria-expanded",String(open));button.textContent=open?"Скрыть":label;});
  wrap.append(button,content);return wrap;
}
function criterionDetails(criterion,report,assessed) {
  const body=node("div","criterion-detail");
  if(criterion.completion_rule||criterion.applies_when)body.append(node("h4","detail-label","Как оценивается"),node("p","small",criterion.completion_rule||criterion.applies_when));
  if(assessed){body.append(node("h4","detail-label","Обоснование"),node("p","criterion-reason",criterion.reason));if(criterion.next_step)body.append(node("p","next-step",`Что улучшить: ${criterion.next_step}`));}
  const speech=assessed&&criterion.id==="C10"?report.speech_review:null;
  if(speech){
    const levels={appropriate:"Деловая речь соответствует нормам",needs_improvement:"Стиль речи требует улучшения",unacceptable:"Есть недопустимые выражения",requires_review:"Контекст речи не оценён",not_applicable:"Нет реплик для оценки речи"};
    body.append(node("strong",`speech-level ${speech.level}`,levels[speech.level]||"Речь не оценена"),node("p","small",speech.summary));
    speech.findings.forEach(finding=>{const item=node("div","speech-finding");appendEvidence(item,finding.evidence);item.append(node("p","",finding.reason),node("p","speech-replacement",`Как можно сказать: ${finding.suggested_phrase}`));body.append(item);});
  }
  const unique=new Set();
  (speech?.findings.length?[]:(assessed?criterion.evidence:criterion.evidence_candidates)).forEach(item=>{const key=item.turn_id+item.quote;if(!unique.has(key)){appendEvidence(body,item);unique.add(key);}});
  if(!assessed&&!unique.size)body.append(node("p","small","Подтверждающие фрагменты не выделены. Проверка учтёт историю разговора и применимость критерия."));
  (report.explanation_evidence?.questions||[]).filter(q=>q.criterion_ids.includes(criterion.id)).forEach(q=>{
    const labels={open:"Вопрос остался открытым",answer_detected:assessed?"Ответ сохранён в истории":"Ответ обнаружен · точность требует проверки",deferred:"Сотрудник не смог ответить · условие осталось неясным",unresolved:"Клиент прекратил уточнения · ответа не получил",meeting_detail:"Подробности перенесены на встречу"};
    const item=node("details","question-history");item.append(node("summary","","История уточняющего вопроса"),node("p","small",`${labels[q.status]||"Ответ не получен"}: ${q.question_meaning}`));
    q.employee_quotes.forEach(quote=>item.append(node("blockquote","",quote)));
    (q.unanswered_employee_quotes||[]).forEach(quote=>item.append(node("blockquote","",quote)));body.append(item);
  });
  if(!assessed)body.append(node("p","small","Здесь показаны кандидаты свидетельств. Оценка проверит смысл реплик, условия и применимость критерия."));
  return body;
}
function renderCriteria(report,assessed) {
  const area=$("criteria");area.replaceChildren();
  const labels={met:"Выполнено",not_met:"Доработать",requires_review:"Не оценено"};
  report.criteria.filter(c=>!c.hidden&&c.status!=="not_applicable").sort((a,b)=>(a.status==="met")-(b.status==="met")).forEach((criterion,index)=>{
    const card=node("article",`criterion ${assessed?criterion.status:"unassessed"}`);card.id=`criterion-${criterion.id}`;
    const top=node("div","criterion-top");top.append(node("span","criterion-number",String(index+1).padStart(2,"0")),node("span",`criterion-status ${assessed?criterion.status:""}`,assessed?labels[criterion.status]:"Без оценки"));
    card.append(top,node("h3","",criterion.title),node("p","criterion-preview",assessed?shortText(criterion.reason):"Результат появится после проверки разговора."),disclosure(criterionDetails(criterion,report,assessed),"Подробнее",`Подробнее: ${criterion.title}`));area.append(card);
  });
}
function renderClaims(report,assessed) {
  const area=$("claim-checks");area.replaceChildren();area.hidden=!assessed;if(!assessed)return;
  area.append(node("h2","","Проверка сказанных условий"),node("p","section-description","Что сотрудник сказал о продукте и чем это подтверждается."));
  if(!report.claims.length){area.append(node("p","small","Продуктовые утверждения для сверки не выделены."));return;}
  const labels={supported:"Подтверждено",contradicted:"Есть ошибка",not_established:"Нет подтверждения",corrected:"Исправлено в разговоре"};
  report.claims.forEach((claim,index)=>{
    const card=node("article",`claim-card ${claim.status}`),head=node("div","claim-heading");
    head.append(node("span","claim-number",String(index+1).padStart(2,"0")),node("span",`claim-status ${claim.status}`,labels[claim.status]));
    card.append(head,node("p","claim-preview",`«${shortText(claim.evidence.quote,170)}»`));
    const body=node("div","claim-detail");appendEvidence(body,claim.evidence);body.append(node("p","",claim.explanation));
    claim.correction.forEach(e=>{body.append(node("p","detail-label",claim.status==="corrected"?"Последующее исправление":"Последующая реплика для проверки"));appendEvidence(body,e);});
    claim.facts.forEach(fact=>{
      const detail=node("details","fact-source");detail.append(node("summary","",`Условие продукта · ${fact.fact_id}`),node("p","small",fact.quote));
      fact.sources.forEach(source=>{detail.append(node("p","source-title",source.title));(source.locations||[]).forEach(location=>detail.append(node("p","small",`Лист «${location.sheet||""}», ячейка ${location.cell||""}: ${location.quote||""}`)));source.urls.forEach(url=>{const link=node("a","source-link","Открыть источник ↗");link.href=url;link.target="_blank";link.rel="noopener noreferrer";detail.append(link);});});body.append(detail);
    });
    card.append(disclosure(body,"Подробнее",`Подробнее об условии ${index+1}`));area.append(card);
  });
}
