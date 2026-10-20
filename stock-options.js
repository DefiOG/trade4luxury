/* Stock / Options Desk: separate feed and underlying-price levels.
   No provider keys or account credentials belong in this browser module. */
(function(root){
"use strict";
const esc=v=>String(v==null?"":v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const finite=v=>typeof v==="number"&&Number.isFinite(v);
const stamp=v=>typeof v==="string"&&/(Z|[+-]\d\d:\d\d)$/.test(v)?Date.parse(v):NaN;
const cash=v=>finite(v)?"$"+v.toFixed(2):"Awaiting level";
const states=["watching","armed","triggered","invalidated","expired"];
function timing(basis){
  return basis==="weekly"?"4–8 weeks":basis==="intraday"?"Weekly: aggressive momentum only":"2–4 weeks";
}
const WEEKLY_OK=["SPY","QQQ","IWM","DIA"];
function spreadCheck(it,now){
  const s=it&&it.creditSpread,why=[];
  if(!s||typeof s!=="object")return {ok:false,why:["No spread published"],credit:null,width:null,maxLoss:null};
  const sh=s.short||{},lg=s.long||{},put=s.type==="put_credit";
  if(s.type!==(it.bias==="bullish"?"put_credit":"call_credit"))why.push("Spread type must match the bias (bullish: put credit, bearish: call credit)");
  const at=stamp(s.verifiedAt),expiry=Date.parse(String(s.expiration)+"T16:00:00-04:00"),days=(expiry-now)/86400000;
  if(!(/^\d{4}-\d\d-\d\d$/.test(s.expiration||"")&&expiry>now))why.push("Invalid expiration");
  else if(days<(WEEKLY_OK.includes(it.ticker)?4:7)||days>45)why.push(WEEKLY_OK.includes(it.ticker)?"Expiry must be 4 to 45 days out":"Expiry must be 7 to 45 days out (weeklies only on SPY, QQQ, IWM, DIA)");
  if(!(Number.isFinite(at)&&at<=now&&now-at<=90000))why.push("Chain quotes are not fresh");
  if(!s.source)why.push("Missing data source");
  if(!(finite(s.underlyingPrice)&&s.underlyingPrice>0))why.push("Missing underlying price");
  const legOk=l=>finite(l.strike)&&l.strike>0&&finite(l.bid)&&l.bid>=0&&finite(l.ask)&&l.ask>0&&l.ask>=l.bid&&finite(l.openInterest)&&l.openInterest>=100&&typeof l.instrumentId==="string"&&l.instrumentId;
  if(!legOk(sh)||!legOk(lg))why.push("Both legs need quotes, open interest of 100+ and an instrument id");
  const width=legOk(sh)&&legOk(lg)?Math.abs(sh.strike-lg.strike):NaN;
  if(Number.isFinite(width)){
    if(put?!(lg.strike<sh.strike):!(lg.strike>sh.strike))why.push("Long leg must be further out of the money than the short leg");
    if(!(width>0&&width<=Math.max(5,0.02*(s.underlyingPrice||0))))why.push("Spread is too wide");
    // Liquidity is judged on the whole spread: the natural credit may give up at most 25% of the mid credit
    // (min 10 cents), and no single leg may be wider than 25% of its own mid (min 20 cents).
    const midCredit=(sh.bid+sh.ask)/2-(lg.bid+lg.ask)/2,natural=sh.bid-lg.ask;
    if(!(midCredit>0&&midCredit-natural<=Math.max(0.1,0.25*midCredit)))why.push("The spread's bid/ask is too wide");
    const legWide=l=>(l.ask-l.bid)>Math.max(0.2,0.25*(l.ask+l.bid)/2);
    if(legWide(sh)||legWide(lg))why.push("A leg's bid/ask is too wide");
    if(!(sh.bid>0))why.push("Short leg has no bid");
    if(finite(it.invalidation)&&(put?sh.strike>it.invalidation:sh.strike<it.invalidation))why.push("Short strike must sit beyond the stock's invalidation level");
  }
  if(!(finite(sh.delta)&&Math.abs(sh.delta)>=0.10&&Math.abs(sh.delta)<=0.30&&Math.sign(sh.delta)===(put?-1:1)))why.push("Short delta must be 0.10 to 0.30");
  if(!(finite(sh.iv)&&sh.iv>0))why.push("Missing implied volatility");
  if(!(finite(sh.volume)&&sh.volume>0))why.push("Short leg has no volume today");
  if(!(typeof s.catalystCheck==="string"&&s.catalystCheck.trim()))why.push("Missing earnings/catalyst check");
  const credit=finite(sh.bid)&&finite(lg.ask)?+(sh.bid-lg.ask).toFixed(2):null;
  if(Number.isFinite(width)&&credit!=null){
    if(!(credit>0&&credit<width))why.push("Credit must be positive and below the width");
    else if(credit<width*0.13)why.push("Credit is under 13% of the width (under 15% return on risk)");
  }
  const maxLoss=Number.isFinite(width)&&credit!=null?+(width-credit).toFixed(2):null;
  return {ok:!why.length,why,credit,width:Number.isFinite(width)?width:null,maxLoss,ror:maxLoss>0&&credit!=null?+(credit/maxLoss).toFixed(3):null};
}
function check(it,now=Date.now()){
  const issues=[];
  if(!it||typeof it!=="object")return {status:"watching",issues:["Missing setup"],chainReady:false,rr:null};
  if(!/^[A-Z][A-Z0-9.-]{0,9}$/.test(it.ticker||""))issues.push("Missing valid ticker");
  if(!["bullish","bearish"].includes(it.bias))issues.push("Bias must be bullish or bearish");
  if(!["daily","weekly","intraday"].includes(it.basis))issues.push("Missing setup timeframe");
  if(!it.timeframeRead?.weekly||!it.timeframeRead?.daily||!it.timeframeRead?.intraday)issues.push("Weekly, daily and intraday chart reads required");
  if(!it.triggerRule)issues.push("Missing technical trigger");
  if(!finite(it.score)||it.score<0||it.score>10)issues.push("Score must be 0–10");
  const levels=[it.trigger,it.invalidation,...(Array.isArray(it.targets)?it.targets:[])];
  if(!Array.isArray(it.targets)||!it.targets.length||!levels.every(v=>finite(v)&&v>0))issues.push("Positive numeric trigger, invalidation and targets required");
  const d=it.bias==="bearish"?-1:1;
  const risk=d*(it.trigger-it.invalidation);
  const reward=Array.isArray(it.targets)?d*(it.targets[0]-it.trigger):NaN;
  const rr=risk>0&&reward>0?reward/risk:null;
  if(rr==null)issues.push("Levels do not match direction");
  else if(it.lane!=="credit"&&rr<(it.lane==="long"?1.5:2))issues.push(it.lane==="long"?"First target offers less than 1.5:1":"First target offers less than 2:1");
  if(Array.isArray(it.targets)&&it.targets.some((v,i)=>i>0&&d*(v-it.targets[i-1])<=0))issues.push("Targets must follow price direction");
  let status=states.includes(it.status)?it.status:"watching";
  const q=it.quote||{},qt=stamp(q.at),fresh=finite(q.price)&&q.price>0&&q.feed==="realtime"&&Number.isFinite(qt)&&qt<=now&&now-qt<=90000;
  if(status==="armed"&&!fresh)issues.push("Fresh real-time quote required to arm");
  if(status==="triggered"){
    const a=it.activation||{},at=stamp(a.at),confirmed=stamp(a.barClosedAt),quoted=stamp(a.quoteAt);
    if(!a.evidence||!finite(a.entry)||a.entry<=0||!Number.isFinite(at)||at>now||
       !Number.isFinite(confirmed)||confirmed>at||at-confirmed>90000||
       !Number.isFinite(quoted)||quoted>at||at-quoted>90000||a.feed!=="realtime"){
      issues.push("Activation requires a completed-bar trigger, real-time quote and timestamped fill reference");
    }
    const actualRisk=d*(a.entry-it.invalidation),actualReward=d*((it.targets||[])[0]-a.entry);
    if(!(actualRisk>0&&actualReward>0&&(it.lane==="credit"||actualReward/actualRisk>=(it.lane==="long"?1.5:2))))issues.push("Activation entry must still offer 2:1");
  }
  if(issues.length&&["armed","triggered"].includes(status))status="watching";
  const c=it.optionCandidate;
  let chainReady=false;
  if(c){
    const at=stamp(c.verifiedAt),expiry=Date.parse(String(c.expiration)+"T16:00:00-04:00");
    chainReady=!!(c.source&&/^\d{4}-\d\d-\d\d$/.test(c.expiration||"")&&expiry>now&&
      Number.isFinite(at)&&at<=now&&now-at<=90000&&finite(c.underlyingPrice)&&c.underlyingPrice>0&&
      ["call","put"].includes(c.type)&&c.type===(it.bias==="bullish"?"call":"put")&&
      finite(c.strike)&&c.strike>0&&finite(c.bid)&&c.bid>0&&finite(c.ask)&&c.ask>=c.bid&&
      finite(c.volume)&&c.volume>0&&finite(c.openInterest)&&c.openInterest>=100&&
      finite(c.iv)&&c.iv>0&&finite(c.delta)&&Math.abs(c.delta)>=0.35&&Math.abs(c.delta)<=0.7&&
      Math.sign(c.delta)===(c.type==="call"?1:-1)&&
      (c.ask-c.bid)/((c.ask+c.bid)/2)<=0.1&&
      Math.abs(c.strike/c.underlyingPrice-1)<=0.05&&
      typeof c.catalystCheck==="string"&&c.catalystCheck.trim().length>0);
    if(chainReady){
      const days=(expiry-now)/86400000;
      if(it.basis==="daily"&&days<14)chainReady=false;
      if(it.basis==="weekly"&&days<28)chainReady=false;
    }
  }
  const spread=it.lane==="credit"?spreadCheck(it,now):null;
  if(issues.length||["invalidated","expired"].includes(status)){chainReady=false;if(spread)spread.ok=false}
  return {status,issues,chainReady,rr,fresh,spread};
}
function candidate(it,checked){
  const c=it.optionCandidate;
  if(!checked.chainReady)return '<p class="small muted"><b>Options:</b> '+esc(timing(it.basis))+' · Contract verification pending. No strike published.</p>';
  return '<div class="small"><b>Contract to watch:</b> '+esc(c.expiration)+' · '+cash(c.strike)+' '+esc(c.type)+
    '<br>Bid '+cash(c.bid)+' / ask '+cash(c.ask)+' · volume '+esc(c.volume)+' · OI '+esc(c.openInterest)+
    '<br>IV '+esc((c.iv*100).toFixed(1))+'% · delta '+esc(c.delta.toFixed(2))+
    '<br><span class="note">Source: '+esc(c.source)+' · '+esc(c.verifiedAt)+' · '+esc(c.catalystCheck)+
    '. Recheck the live chain before trading. Premium is quoted per share.</span></div>';
}
function spreadBlock(it,v){
  const s=it.creditSpread;if(it.lane!=="credit")return "";
  if(!s)return '<p class="small muted"><b>Credit spread:</b> no spread published yet.</p>';
  const sp=v.spread||{},sh=s.short||{},lg=s.long||{},put=s.type==="put_credit",k=put?"P":"C";
  return '<div class="small"><b>'+(put?"Put":"Call")+' credit spread:</b> sell '+cash(sh.strike)+k+' / buy '+cash(lg.strike)+k+' · exp '+esc(s.expiration)+
    '<br>'+(sp.credit!=null?'Credit '+cash(sp.credit)+' · max loss '+cash(sp.maxLoss)+' (per share, ×100 per spread)':'')+
    ' · short delta '+esc(finite(sh.delta)?sh.delta.toFixed(2):"–")+
    (sp.ok?'<br><span class="pos">Passes the spread checks.</span>':'<br><span class="note">Not published as a trade: '+(sp.why||[]).map(esc).join("; ")+'.</span>')+'</div>';
}
function ticket(it,now){
  const v=check(it,now),side=it.bias==="bearish"?"put":"call",disabled=["invalidated","expired"].includes(v.status);
  return '<article class="ticket"><div class="t-h"><span class="sym">'+esc(it.ticker||"Unknown")+'</span>'+
    '<span class="chip '+(disabled?"":it.bias==="bullish"?"long":"short")+'">'+esc(it.bias||"Unclassified")+'</span>'+
    '<span class="chip">'+esc(v.status)+'</span></div><div class="t-b">'+
    '<h3>'+esc(it.setup||"Chart watch")+'</h3><p>'+esc(it.thesis||"")+'</p>'+
    '<p class="small"><b>Current zone:</b> '+esc(it.currentZone||"Awaiting chart")+'<br><b>Support:</b> '+esc(it.support||"Awaiting chart")+'</p>'+
    '<div class="levels"><div><span>Stock trigger</span><b>'+cash(it.trigger)+'</b></div><div><span>Invalidation</span><b>'+cash(it.invalidation)+'</b></div>'+
    '<div><span>First target</span><b>'+cash((it.targets||[])[0])+'</b></div><div><span>Planned R:R</span><b>'+(v.rr==null?"—":v.rr.toFixed(2)+":1")+'</b></div></div>'+
    '<p class="small"><b>Best entry:</b> '+esc(it.triggerRule||"Awaiting confirmation")+'</p>'+
    '<p class="small"><b>Targets:</b> '+(Array.isArray(it.targets)?it.targets:[]).map(cash).join(" · ")+'</p>'+
    (v.status==="triggered"?'<p class="small"><b>Trigger recorded:</b> '+esc(it.activation.at)+' at underlying '+cash(it.activation.entry)+'. This is a recorded trigger, not a current entry quote.</p>':"")+
    '<details><summary class="small">Timeframe read</summary><p class="small">Weekly: '+esc(it.timeframeRead?.weekly||"Missing")+
    '<br>Daily: '+esc(it.timeframeRead?.daily||"Missing")+'<br>Intraday: '+esc(it.timeframeRead?.intraday||"Missing")+'</p></details>'+
    (it.lane==='credit'?spreadBlock(it,v):candidate(it,v))+'<p class="note">'+esc(it.riskNote||"If the underlying loses invalidation, the setup is dead. Option premium can fall even when the stock moves favorably.")+'</p>'+
    (disabled?'<p class="small neg">This setup is '+esc(v.status)+'.</p>':"")+
    (v.issues.length?'<p class="note">Watching: '+v.issues.map(esc).join("; ")+'.</p>':"")+
    '<p class="note">Underlying quote: '+cash(it.quote?.price)+' · '+esc(it.quote?.at||"No timestamp")+' · '+esc(it.quote?.source||"No source")+
    (it.quote?.feed!=="realtime"?' · Not a verified real-time feed':!v.fresh?' · Historical snapshot; refresh before entry':"")+
    '</p></div><div class="t-f small">'+esc(side==="call"?"Call-side watch":"Put-side watch")+' · '+esc(timing(it.basis))+'</div></article>';
}
function render(el,desk,now=Date.now()){
  const scans=(Array.isArray(desk?.scans)?desk.scans:[]).filter(s=>s&&typeof s==="object")
    .sort((a,b)=>String(b.createdAt||b.date||"").localeCompare(String(a.createdAt||a.date||"")));
  const latest=scans[0],ideas=(Array.isArray(latest?.ideas)?latest.ideas:[]).filter(i=>i&&typeof i==="object");
  const counts={watching:0,armed:0,triggered:0,invalidated:0,expired:0};
  ideas.forEach(it=>counts[check(it,now).status]++);
  const status="watching → armed → triggered → invalidated / expired";
  el.innerHTML='<div class="panel"><div class="panel-h"><div><div class="eyebrow">Trade for Luxury</div><h2>Stock / Options Desk</h2></div><span class="chip">Chart first · contract second</span></div>'+
    '<p class="muted">Weekly structure. Daily setup. Intraday entry confirmation. Stock-price levels determine the trigger and invalidation; the current option chain determines the contract.</p>'+
    '<p class="small">'+esc(status)+'</p></div>'+
    '<div class="stats">'+["watching","armed","triggered","invalidated"].map(k=>'<div class="stat"><div class="eyebrow">'+esc(k)+'</div><b>'+counts[k]+'</b></div>').join("")+'</div>'+
    (!ideas.length?'<div class="panel"><h3>Waiting for the first sourced scan</h3><p class="muted">No stock or option calls have been published here yet. Live triggers begin after the market-data connection is ready.</p><p class="small">Each setup will show support, reclaim or breakout trigger, targets, invalidation, timeframe reads and an options verification status.</p></div>':
    '<div class="panel"><div class="eyebrow">'+esc(latest.date)+' · '+esc(latest.createdAt||"")+'</div><h3>'+esc(latest.headline||"Latest stock watchlist")+'</h3><p class="muted">'+esc(latest.summary||"")+'</p>'+
    '<label for="so-filter">Show setups</label><select id="so-filter" class="btn"><option value="all">All setups</option><option value="bullish">Call-side watches</option><option value="bearish">Put-side watches</option></select></div>'+
    '<div id="so-tickets" class="tickets">'+ideas.map(it=>ticket(it,now)).join("")+'</div>')+
    '<div class="panel"><h3>Results</h3><p class="muted">Stock-trigger outcomes and option-contract returns are separate. No options performance is inferred from stock moves. This desk has no automated results yet.</p></div>'+
    '<p class="note">Not financial advice. Just my chart read. Contract liquidity and quotes must be verified before publishing an option idea.</p>';
  const filter=el.querySelector("#so-filter");
  if(filter)filter.addEventListener("change",()=>{el.querySelector("#so-tickets").innerHTML=ideas.filter(it=>filter.value==="all"||it.bias===filter.value).map(it=>ticket(it,Date.now())).join("")});
}
root.StockOptionsDesk={check,spreadCheck,timing,render};
})(typeof window!=="undefined"?window:globalThis);
