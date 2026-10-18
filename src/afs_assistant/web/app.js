"use strict";
const $ = id => document.getElementById(id);
const make = (tag, text, cls) => {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;};
let current = null, busy = false, outbox = null, poll = null, renderedTurn = null;
function error(message) {$("error").textContent = message; $("error").hidden = !message;}
async function api(path, method = "GET", body) {
  const response = await fetch(`/api${path}`, {method, headers: body ? {"Content-Type":"application/json"} : {}, body: body ? JSON.stringify(body) : undefined});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Please check your message and try again.");
  return value;
}
function controls() {
  const pending = current?.turns.at(-1)?.status === "pending";
  $("send").disabled = busy || pending; $("message").disabled = busy || pending;
  $("new-chat").disabled = busy;
  document.querySelectorAll("#chat-list button,.suggestions button,.retry").forEach(button => button.disabled = busy);
  $("status").textContent = busy || pending ? "Preparing an answer…" : "Answers link to the available documents.";
}
async function listChats() {
  const chats = await api("/chats"); $("chat-list").replaceChildren();
  for (const chat of chats) {
    const button = make("button", chat.text.length > 58 ? chat.text.slice(0,58) + "…" : chat.text, current?.id === chat.id ? "selected" : "");
    button.type = "button";
    button.onclick = async () => {if (busy) return; try {outbox=null; render(await api(`/chats/${chat.id}`)); await listChats();} catch(e) {error(e.message);}};
    $("chat-list").append(button);
  }
  controls();
}
function bubble(who, text) {
  const article = make("article", undefined, `message ${who === "You" ? "user" : "assistant"}`);
  article.append(make("div",who,"message-label")); if (text) article.append(make("p",text));
  $("messages").append(article); return article;
}
function sourceLabel(source) {
  if (source.citation_label) return source.citation_label;
  const shortTitle = source.title.match(/AN\s*\d+/i)?.[0] || source.source_id;
  const location = source.locator.label.replace(/^PDF\s*/i, "");
  return `${shortTitle} · ${location}${source.element_type === "image" ? " · image" : ""}`;
}
function tableFields(item) {
  const data=item.structured_data;
  return item.element_type === "table_row" && data && typeof data === "object" && !Array.isArray(data)
    ? Object.entries(data) : [];
}
function sourcePreview(item) {
  if (item.image) return item.image.caption;
  if (tableFields(item).length) {
    const data=item.structured_data;
    const values=[data.method,data.measurement_technique,data.sample_type].filter(value=>value != null && value !== "");
    return values.length ? values.join(" · ") : "Table row · Open to read all fields";
  }
  return item.text;
}
function sourceTable(item) {
  const fields=tableFields(item);
  if (!fields.length) return null;
  const table=make("table",undefined,"source-table");
  table.append(make("caption","Cited table row"));
  const head=make("thead"), headings=make("tr");
  for (const label of ["Property","Source value"]) {
    const cell=make("th",label); cell.scope="col"; headings.append(cell);
  }
  head.append(headings); table.append(head);
  const body=make("tbody");
  const labels={method_no:"Method number",measurement_technique:"Measurement technique",
    physical_phenomenon:"Physical phenomenon",sample_type:"Sample type",optical_path_or_depth:"Optical path / depth"};
  for (const [key,value] of fields) {
    const row=make("tr"), label=labels[key] || key.replaceAll("_"," ").replace(/^./,letter=>letter.toUpperCase());
    const heading=make("th",label); heading.scope="row";
    const text=value == null || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
    row.append(heading,make("td",text)); body.append(row);
  }
  table.append(body);
  return table;
}
function sourceCardId(turnId,id) {return "source-"+turnId+"-"+id;}
function sourceCards(sources,chatId,turnId) {
  const cards=make("div",undefined,"source-cards");
  Object.entries(sources).forEach(([id,source],index)=>{
    const card=disclosure({id:chatId},{id:turnId},"source:"+id,"source-card");
    card.id=sourceCardId(turnId,id);
    const summary=card.querySelector("summary");
    summary.setAttribute("aria-label","Source "+(index+1)+": "+sourceLabel(source));
    summary.replaceChildren(make("span","["+(index+1)+"] "+sourceLabel(source),"source-card-label"),
      make("span",source.title,"source-card-title"));
    const preview=make("span",undefined,"source-card-preview");
    preview.append(make("span",sourcePreview(source),"source-card-excerpt"));
    if (source.image || /^\s*(?:fig\.?|figure)\s*\d+\b/i.test(source.text)) {
      const image=make("img",undefined,"source-card-image");
      image.alt=source.image?.caption || "Figure associated with this caption";
      image.loading="lazy"; image.hidden=true;
      image.onload=()=>{image.hidden=false;};
      image.onerror=()=>image.remove();
      image.src="/api/chats/"+chatId+"/turns/"+turnId+"/sources/"+encodeURIComponent(id)+"/image";
      preview.prepend(image);
    }
    summary.append(preview);
    const body=make("div",undefined,"source-body"); card.append(body);
    let state="idle";
    const load=async()=>{
      if (state !== "idle") return;
      state="loading";
      state=await loadSource(body,chatId,turnId,id) ? "loaded" : "idle";
    };
    card.addEventListener("toggle",()=>{if (card.open) load();});
    cards.append(card);
    if (card.open) load();
  });
  return cards;
}
function openSource(chatId, turnId, id) {
  if (current?.id !== chatId) return;
  const card=$(sourceCardId(turnId,id));
  if (!card) return;
  card.closest(".source-list").open=true;
  card.open=true;
  card.querySelector("summary").focus({preventScroll:true});
  card.scrollIntoView({behavior:"smooth",block:"start"});
}
async function loadSource(content, chatId, turnId, id) {
  content.replaceChildren(make("p","Loading source…"));
  try {
    const source = await api(`/chats/${chatId}/turns/${turnId}/sources/${encodeURIComponent(id)}`);
    const item = source.evidence;
    content.replaceChildren();
    if (item.worksheet) content.append(make("p",item.worksheet + " · " + item.cell_range,"meta"));
    if (item.synthetic) content.append(make("p","Fictional demo evidence","meta"));
    if (source.image_url) {
      const figure=item.image || item.display_figure.image;
      const image=make("img"); image.src=source.image_url; image.alt=figure.caption || "Document image chunk";
      content.append(image);
      if (figure.caption) content.append(make("p",item.display_figure ? item.text : figure.caption,"excerpt"));
      if (figure.description) {
        const details=make("details");
        details.append(make("summary","AI-generated visual description"),make("p",figure.description.text,"excerpt"));
        content.append(details);
      }
      if (figure.review_notes?.length) {
        const details=make("details"); details.append(make("summary","Visual review notes"));
        figure.review_notes.forEach(note=>details.append(make("p",note,"meta"))); content.append(details);
      }
    } else content.append(sourceTable(item) || make("p",item.text,"excerpt"));
    const links=make("div",undefined,"source-actions");
    for (const [url,label] of [[source.original_url,"Open document ↗"],
        [source.page_url,"Open saved page ↗"],[source.image_url,"Open image ↗"]]) {
      if (!url) continue;
      const link=make("a",label); link.href=url; link.target="_blank"; link.rel="noopener noreferrer";
      links.append(link);
    }
    content.append(links);
    return true;
  } catch(e) {content.replaceChildren(make("p",e.message)); return false;}
}
function fieldRefs(turn, path, ids=[]) {
  const precise=(turn.answer.citations || [])
    .filter(citation=>citation.field === path).flatMap(citation=>citation.evidence_ids);
  return [...new Set(precise.length ? precise : ids)];
}
function sourceButton(id, chat, turn) {
  const source=turn.sources[id];
  const number=Object.keys(turn.sources).indexOf(id)+1;
  const button=make("button","["+number+"]","source-link");
  button.type="button"; button.title=sourceLabel(source) + " — " + source.text.slice(0,180);
  button.setAttribute("aria-label","Source "+number+": "+sourceLabel(source));
  button.setAttribute("aria-controls",sourceCardId(turn.id,id));
  button.onclick=()=>openSource(chat.id,turn.id,id);
  return button;
}
function appendSources(container, ids, chat, turn, path="", shown=new Set()) {
  for (const id of fieldRefs(turn,path,ids || [])) {
    if (!turn.sources[id] || shown.has(id)) continue;
    container.append(sourceButton(id,chat,turn));
  }
}
// Paragraph text carries [n] markers beside the sentences they support.
function inlineParagraph(text, chat, turn) {
  const paragraph=make("p"), shown=new Set(), ids=Object.keys(turn.sources);
  for (const piece of text.split(/(\[\d+\])/)) {
    const match=piece.match(/^\[(\d+)\]$/), id=match && ids[Number(match[1])-1];
    if (id) { paragraph.append(sourceButton(id,chat,turn)); shown.add(id); }
    else if (piece) paragraph.append(document.createTextNode(piece));
  }
  return {paragraph, shown};
}
function appendList(container, title, values, ordered=false) {
  const items=[...new Set(values.filter(Boolean))]; if (!items.length) return;
  container.append(make("h3",title));
  const list=make(ordered ? "ol" : "ul");
  items.forEach(text=>list.append(make("li",text))); container.append(list);
}
const openDisclosures=new Set(), knownDisclosures=new Set();
function disclosure(chat,turn,name,cls="",defaultOpen=false) {
  const key=chat.id+":"+turn.id+":"+name;
  const details=make("details",undefined,cls);
  if (!knownDisclosures.has(key)) {
    knownDisclosures.add(key); if (defaultOpen) openDisclosures.add(key);
  }
  details.open=openDisclosures.has(key);
  details.append(make("summary",name));
  details.addEventListener("toggle",()=>{
    if (!details.isConnected) return;
    if (details.open) openDisclosures.add(key); else openDisclosures.delete(key);
  });
  return details;
}
function renderActivity(container,chat,turn) {
  if (!turn.trace?.length) return;
  const details=disclosure(chat,turn,"Activity","activity");
  const list=make("ol"), seen=new Set(), documents=new Map();
  for (const event of turn.trace.filter(entry=>entry.stage === "evidence")) {
    const group=documents.get(event.source_id) || {ids:new Set(),title:event.title};
    (event.evidence_ids || [event.reference]).forEach(id=>group.ids.add(id));
    group.title=group.title || event.title || Object.values(turn.sources || {}).find(source=>source.source_id === event.source_id)?.title;
    documents.set(event.source_id,group);
  }
  const add=(key,text)=>{
    if (seen.has(key)) return; seen.add(key); list.append(make("li",text));
  };
  for (const event of turn.trace) {
    if (event.stage === "routing") add("routing","Understanding the question.");
    if (event.stage === "search") add("search:"+event.query,"Found "+event.candidate_count+" possible passages in the supplied material.");
    if (event.stage === "evidence") {
      const group=documents.get(event.source_id), count=group.ids.size;
      add("read:"+event.source_id,"Reviewed "+count+(count === 1 ? " passage" : " passages")+" from "+(group.title || event.source_id || "the supplied material")+".");
    }
    if (event.stage === "visual_inspection") add("image","Inspecting an original document image.");
    if (event.stage === "formatting") add("writing","Writing the answer from the inspected material.");
    if (event.stage === "verification") add("checking","Checking source references and numerical details.");
    if (event.stage === "completed") add("completed","Answer prepared with "+Object.keys(turn.sources || {}).length+" cited passages.");
    if (event.stage === "failed") add("failed","The answer could not be completed. See the message above.");
  }
  details.append(list); container.append(details);
}
function renderStudy(container,experiment,chat,turn) {
    container.append(make("h3","First feasibility study"));
    const objective=make("p",experiment.objective);
    appendSources(objective,[],chat,turn,"assessment.experiment.objective"); container.append(objective);
    const steps=make("ol");
    experiment.steps.forEach((step,index)=>{
      const item=make("li",step.replace(/^\s*\d+[.)]\s*/,""));
      appendSources(item,[],chat,turn,"assessment.experiment.steps["+index+"]"); steps.append(item);
    }); container.append(steps);
    if (experiment.criteria_to_confirm.length) {
      container.append(make("h3","Criteria to agree and check"));
      const list=make("ul");
      experiment.criteria_to_confirm.forEach((text,index)=>{
        const item=make("li",text); appendSources(item,[],chat,turn,"assessment.experiment.criteria_to_confirm["+index+"]"); list.append(item);
      }); container.append(list);
    }
}
function renderAssessment(container, assessment, chat, turn) {
  const section=make("section",undefined,"assessment");
  if (!turn.answer.parts.some(part=>part.kind === "answer")) section.append(make("p",assessment.summary));
  if (assessment.requirements?.length) {
    const context=disclosure(chat,turn,"Process requirements and constraints","customer-context");
    const list=make("ul");
    for (const requirement of assessment.requirements) {
      const item=make("li",requirement.statement);
      item.append(make("blockquote",requirement.user_quote)); list.append(item);
    }
    context.append(list);
    appendList(context,"Not provided",assessment.unknowns || []);
    section.append(context);
  }
  // Drop exact and near repeats: a risk whose content words an earlier line already covers.
  const stop=new Set(["a","an","the","and","or","of","for","to","in","on","with","is","are","be","may","can","this","that","it","its","as","by","at","from","not","if","whether","e","g"]);
  const seen=[];
  const fresh=text=>{
    const words=new Set((text.toLowerCase().match(/[a-z0-9]+/g)||[]).filter(word=>!stop.has(word)));
    const repeat=seen.some(earlier=>{
      const shared=[...words].filter(word=>earlier.has(word)).length, smaller=Math.min(words.size,earlier.size);
      return (shared===words.size && shared===earlier.size) || (smaller>=3 && shared/smaller>=0.8);
    });
    if (!words.size || repeat) return false;
    seen.push(words); return true;
  };
  // A gap paragraph already shown in the answer is not repeated under shared risks.
  turn.answer.parts.filter(part=>part.kind==="gap").forEach(part=>fresh(part.text));
  const appendNotes=(parent,title,notes)=>{
    const kept=notes.filter(note=>fresh(note.text));
    if (!kept.length) return;
    parent.append(make("h4",title));
    const list=make("ul");
    kept.forEach(note=>{const item=make("li",note.text); appendSources(item,[],chat,turn,note.path); list.append(item);});
    parent.append(list);
  };
  if (assessment.candidates.length) section.append(make("h3","Approaches worth testing"));
  for (const [index,candidate] of assessment.candidates.entries()) {
    const path="assessment.candidates["+index+"]";
    const card=make("div",undefined,"candidate");
    card.append(make("h4",candidate.name));
    for (const [claimIndex,claim] of candidate.claims.entries()) {
      const paragraph=make("div",undefined,"claim"); paragraph.append(make("p",claim.text));
      appendSources(paragraph,claim.evidence_ids,chat,turn,path+".claims["+claimIndex+"].text"); card.append(paragraph);
    }
    if (candidate.measurement_concept) {
      card.append(make("h4","Measurement / probe concept"),make("p",candidate.measurement_concept));
      appendSources(card,candidate.evidence_ids,chat,turn,path+".measurement_concept");
    }
    if (candidate.spectral_range) {
      card.append(make("h4","Spectral range"),make("p",candidate.spectral_range));
      appendSources(card,candidate.evidence_ids,chat,turn,path+".spectral_range");
    }
    appendNotes(card,"Limitations",candidate.limitations.map((text,n)=>({text,path:path+".limitations["+n+"]"})));
    appendNotes(card,"Assumptions to confirm",candidate.assumptions.map((text,n)=>({text,path:path+".assumptions["+n+"]"})));
    section.append(card);
  }
  appendList(section,"Important clarification questions",
    assessment.questions.map(question=>question.question + " " + question.affects));
  const gaps=assessment.gaps.map((text,index)=>({text,path:"assessment.gaps["+index+"]"})).filter(note=>fresh(note.text));
  if (gaps.length) {
    section.append(make("h3","Shared risks and evidence gaps"));
    const list=make("ul");
    gaps.forEach(note=>{const item=make("li",note.text); appendSources(item,[],chat,turn,note.path); list.append(item);});
    section.append(list);
  }
  if (assessment.experiment) renderStudy(section,assessment.experiment,chat,turn);
  container.append(section);
}
function render(chat) {
  const latest=chat.turns.at(-1);
  const turnKey=latest ? `${chat.id}:${latest.id}:${latest.status}` : null;
  const scrollToLatest=turnKey && turnKey !== renderedTurn;
  renderedTurn=turnKey;
  current=chat; localStorage.setItem("afs-chat",chat.id); clearTimeout(poll);
  $("welcome").hidden=true; $("messages").replaceChildren();
  if (!chat.turns.length || chat.turns[0].text !== chat.text) bubble("You",chat.text);
  for (const turn of chat.turns) {
    bubble("You",turn.text).id=`turn-${turn.id}`; const reply=bubble("AFS");
    if (turn.status === "pending") {
      const stage=turn.trace?.at(-1)?.stage;
      const labels={routing:"Understanding your question…",query_plan:"Planning the investigation…",investigating:"Investigating the supplied material…",tool:"Checking sources…",search:"Reviewing search results…",evidence:"Reading source evidence…",formatting:"Preparing the answer…"};
      labels.verification="Checking citations and quantities…";
      labels.visual_inspection="Inspecting the original figure…";
      labels.tool_call_retry="Requesting source inspection…";
      reply.append(make("p",labels[stage] || "Preparing an answer…","muted"));
    }
    else if (turn.answer) {
      const assessment=turn.answer.assessment;
      if (assessment) {
        const status={ready_for_review:"Provisional feasibility assessment",
          awaiting_clarification:"Further clarification needed",incomplete:"Insufficient supporting evidence"};
        reply.append(make("p",status[assessment.status],"assessment-status"));
      }
      for (const [index,part] of turn.answer.parts.entries()) {
        const block=make("div",undefined,"part " + part.kind);
        const {paragraph, shown}=inlineParagraph(part.text,chat,turn);
        block.append(paragraph);
        appendSources(block,part.evidence_ids,chat,turn,"parts["+index+"].text",shown);
        reply.append(block);
      }
      if (assessment) {
        if (assessment.experiment && !turn.answer.parts.some(part=>part.kind === "proposal")) {
          renderStudy(reply,assessment.experiment,chat,turn);
        }
        const report=disclosure(chat,turn,"Full assessment","full-assessment");
        renderAssessment(report,assessment,chat,turn); reply.append(report);
        const exports=disclosure(chat,turn,"Download this assessment","assessment-exports");
        for (const [format,label] of [["md","Assessment report (.md)"],["json","Structured assessment (.json)"]]) {
          const link=make("a",label);
          link.href="/api/chats/" + chat.id + "/turns/" + turn.id + "/export?format=" + format;
          exports.append(make("p")); exports.append(link);
        }
        reply.append(exports);
      }
    } else {
      const code=turn.trace?.findLast(entry=>entry.stage === "failed")?.code;
      const messages={
        timeout:"The model service took too long to respond. Your question is saved; please retry.",
        connection:"The model service could not be reached. Your question is saved; please retry.",
        provider_rejected:"The model service rejected the request. Your question is saved; please retry after the service is available.",
        answer_validation:"I couldn’t prepare a reliably sourced answer this time. Your question is saved; you can try again."
      };
      reply.append(make("p",messages[code] || "I couldn’t complete this answer because of an application error. Your question is saved; you can retry.","failure"));
      if (turn.id === chat.turns.at(-1).id) {
        const button=make("button","Try again","retry"); button.type="button";
        button.onclick=()=>run(async()=>{render(await api(`/chats/${chat.id}/turns/${turn.id}/retry`,"POST",{expected_revision:current.revision}));}); reply.append(button);
      }
    }
    if (turn.status === "answered" && Object.keys(turn.sources || {}).length) {
      const references=disclosure(chat,turn,"Sources · "+Object.keys(turn.sources).length,"source-list",true);
      references.append(sourceCards(turn.sources,chat.id,turn.id));
      reply.append(references);
    }
    renderActivity(reply,chat,turn);
  }
  controls();
  if (scrollToLatest) $( `turn-${latest.id}` ).scrollIntoView({behavior:"smooth",block:"start"});
  if (chat.turns.at(-1)?.status === "pending") poll=setTimeout(async()=>{
    if (current?.id !== chat.id || busy) return;
    try {render(await api(`/chats/${chat.id}`));} catch(e) {error(e.message);}
  },3000);
}
async function run(fn) {
  if (busy) return; busy=true; error(""); controls();
  try {await fn(); await listChats();} catch(e) {error(e.message || "Request failed. Please retry.");}
  finally {busy=false; controls();}
}
$("composer").onsubmit=event=>{event.preventDefault(); const text=$("message").value.trim(); if (!text) return;
  run(async()=>{
    if (!outbox || outbox.text !== text || outbox.chatId !== (current?.id || null)) outbox={text,request_id:crypto.randomUUID(),chatId:current?.id || null,revision:current?.revision};
    $("welcome").hidden=true; const question=bubble("You",text); const pending=bubble("AFS","Preparing an answer…"); pending.id="pending-answer"; question.scrollIntoView({behavior:"smooth",block:"start"});
    try {
      const value=outbox.chatId ? await api(`/chats/${outbox.chatId}/messages`,"POST",{text,request_id:outbox.request_id,expected_revision:outbox.revision}) : await api("/chats","POST",{text,request_id:outbox.request_id});
      render(value); $("message").value=""; outbox=null;
    } catch(e) {if (current) render(current); else $("messages").replaceChildren(); throw e;}
  });
};
$("new-chat").onclick=()=>{if (busy) return; clearTimeout(poll); current=null;outbox=null; localStorage.removeItem("afs-chat");localStorage.removeItem("afs-case");$("messages").replaceChildren();$("welcome").hidden=false;$("message").value="";error("");controls();$("message").focus();listChats().catch(e=>error(e.message));};
$("message").onkeydown=event=>{if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); if (!$("send").disabled) $("composer").requestSubmit();}};
for (const button of document.querySelectorAll("[data-question]")) button.onclick=()=>{$("message").value=button.dataset.question;$("message").focus();};
run(async()=>{
  const health=await api("/health");$("environment").textContent=health.mode === "demo" ? "Demo" : "Document Q&A";
  if (!health.evidence_ready) error("No documents are indexed yet.");
  const id=localStorage.getItem("afs-chat") || localStorage.getItem("afs-case");
  if (id) {try {render(await api(`/chats/${id}`));} catch {localStorage.removeItem("afs-chat");localStorage.removeItem("afs-case");}}
});
