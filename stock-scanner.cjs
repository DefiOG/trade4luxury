/* Pure stock setup scanner. Consumes provider-normalized, completed OHLCV bars.
   A quote alone cannot trigger. This module never submits orders. */
(function(root){
"use strict";
const finite=x=>typeof x==="number"&&Number.isFinite(x);
const parse=s=>typeof s==="string"&&/(Z|[+-]\d\d:\d\d)$/.test(s)?Date.parse(s):NaN;
const average=a=>a.reduce((x,y)=>x+y,0)/a.length;
const day=t=>new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(t));
function bars(rows,now){
  if(!Array.isArray(rows))throw Error("Missing candle series");
  let prior=-Infinity;
  return rows.filter(b=>{
    const t=parse(b.closedAt);
    if(!Number.isFinite(t)||t<=prior)throw Error("Candles must have unique ordered close timestamps");
    prior=t;
    if(![b.open,b.high,b.low,b.close,b.volume].every(finite)||b.low<=0||b.volume<0||
       b.high<Math.max(b.open,b.close)||b.low>Math.min(b.open,b.close)||b.low>b.high)throw Error("Invalid OHLCV candle");
    return b.complete===true&&t<=now;
  });
}
function sma(b,n){return b.length>=n?average(b.slice(-n).map(x=>x.close)):null}
function atr(b,n=14){
  if(b.length<n+1)return null;
  return average(b.slice(-n).map((x,i)=>Math.max(x.high-x.low,Math.abs(x.high-b[b.length-n+i-1].close),Math.abs(x.low-b[b.length-n+i-1].close))));
}
function pivots(b,type){
  return b.filter((x,i)=>i>=2&&i<b.length-2&&[b[i-2],b[i-1],b[i+1],b[i+2]].every(y=>type==="high"?x.high>y.high:x.low<y.low)).map(x=>x[type]);
}
function scan(input,now=Date.now(),config={}){
  const tick=config.tickSize??0.01,volumeMultiple=config.volumeMultiple??1.2,minScore=config.minScore??6;
  if(!finite(tick)||tick<=0||!finite(volumeMultiple)||volumeMultiple<1)throw Error("Invalid scanner configuration");
  if(!/^[A-Z][A-Z0-9.-]{0,9}$/.test(input.ticker||""))throw Error("Invalid stock ticker");
  const d=bars(input.daily,now),w=bars(input.weekly,now),m=bars(input.fifteenMinute,now),f=bars(input.fiveMinute,now);
  if(d.length<51||w.length<21||m.length<6||f.length<21)throw Error("Need 51 daily, 21 weekly, 6 fifteen-minute and 21 five-minute completed bars");
  const q=input.quote||{},time=parse(q.at),last=f.at(-1),prev=f.at(-2),latest15=m.at(-1),range=atr(d);
  const d20=sma(d,20),d50=sma(d,50),w20=sma(w,20),weekly=w.at(-1).close;
  const bullish=d.at(-1).close>d20&&d20>d50&&weekly>w20;
  const bearish=d.at(-1).close<d20&&d20<d50&&weekly<w20;
  const bias=bearish?"bearish":"bullish",sign=bias==="bullish"?1:-1;
  const block=[];
  const events=input.events||{},eventChecked=parse(events.checkedAt),eventAt=events.nextMajorAt==null?null:parse(events.nextMajorAt);
  if(!events.source||!Number.isFinite(eventChecked)||eventChecked>now||now-eventChecked>86400000||
     !["clear","scheduled"].includes(events.status)||(events.status==="scheduled"&&!Number.isFinite(eventAt)))block.push("Fresh earnings/major-event check required");
  if(events.status==="scheduled"&&Number.isFinite(eventAt)&&eventAt>=now&&eventAt-now<=1800000)block.push("Major event within 30 minutes: wait for the release");
  if(!bullish&&!bearish)block.push("Weekly and daily trend do not agree");
  if(!finite(range)||range<=0)throw Error("ATR unavailable");
  const recent=m.slice(-5),prior15=m.slice(-6,-1);
  const rawTrigger=bias==="bullish"?Math.max(...prior15.map(x=>x.high))+tick:Math.min(...prior15.map(x=>x.low))-tick;
  const trigger=+(bias==="bullish"?Math.ceil(rawTrigger/tick)*tick:Math.floor(rawTrigger/tick)*tick).toFixed(6);
  const rawStop=bias==="bullish"?Math.min(...recent.map(x=>x.low))-0.1*range:Math.max(...recent.map(x=>x.high))+0.1*range;
  const stop=+(bias==="bullish"?Math.floor(rawStop/tick)*tick:Math.ceil(rawStop/tick)*tick).toFixed(6);
  const resistance=[...pivots(d.slice(-90),"high"),...pivots(w.slice(-52),"high")];
  const support=[...pivots(d.slice(-90),"low"),...pivots(w.slice(-52),"low")];
  const structure=(bias==="bullish"?resistance:support).filter(x=>sign*(x-trigger)>0).sort((a,b)=>sign*(a-b));
  const targets=[...new Set(structure)].slice(0,3);
  if(!targets.length)block.push("No confirmed opposing structure for target");
  const plannedRisk=sign*(trigger-stop),rr=targets.length?sign*(targets[0]-trigger)/plannedRisk:null;
  if(!(plannedRisk>0&&rr>=2))block.push("First structural target does not offer 2:1");
  if(Math.abs(d.at(-1).close-d20)>2*range)block.push("Daily price extended beyond two ATR from 20-day average");
  const quoteFresh=finite(q.price)&&q.price>0&&Number.isFinite(time)&&time<=now&&now-time<=90000&&q.feed==="realtime";
  if(!quoteFresh)block.push("Fresh real-time quote unavailable");
  const barTime=parse(last.closedAt),mTime=parse(latest15.closedAt);
  if(now-barTime>390000||now-mTime>990000)block.push("Intraday candles stale");
  // VWAP uses only this regular session's supplied candles. Coverage must be explicit.
  const session=day(now),sessionBars=f.filter(x=>x.session==="regular"&&day(parse(x.closedAt))===session);
  const clock=new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const sessionComplete=sessionBars.length>0&&clock.format(new Date(parse(sessionBars[0].closedAt)))==="09:35"&&sessionBars.every((b,i)=>!i||parse(b.closedAt)-parse(sessionBars[i-1].closedAt)===300000);
  if(!sessionComplete)block.push("Complete regular-session five-minute history required for VWAP");
  const volume=sessionBars.reduce((a,b)=>a+b.volume,0);
  const vwap=volume>0?sessionBars.reduce((a,b)=>a+((b.high+b.low+b.close)/3)*b.volume,0)/volume:null;
  const baseline=f.slice(-21,-1).filter(x=>x.session==="regular"&&day(parse(x.closedAt))===session);
  const avgVolume=baseline.length>=10?average(baseline.map(x=>x.volume)):null;
  const volumePass=avgVolume>0&&last.volume>=volumeMultiple*avgVolume;
  const vwapPass=vwap!=null&&sign*(last.close-vwap)>0;
  const crossed=sign*(prev.close-trigger)<=0&&sign*(last.close-trigger)>0;
  const aligned15=sign*(latest15.close-latest15.open)>0;
  const fullMarket=q.coverage==="consolidated";
  if(!fullMarket)block.push("Consolidated coverage required for volume/VWAP confirmation");
  const score=(bullish||bearish?2:0)+(rr>=2?1:0)+(Math.abs(d.at(-1).close-d20)<=2*range?1:0)+(aligned15?1:0)+(vwapPass?1:0)+(volumePass?1:0)+(crossed?1:0);
  if(score<minScore)block.push("Technical score below threshold");
  const actualRisk=sign*(q.price-stop),actualReward=targets.length?sign*(targets[0]-q.price):null;
  const geometry=actualRisk>0&&actualReward>0&&actualReward/actualRisk>=2;
  let status=block.length?"watching":"armed";
  if(!block.length&&crossed&&aligned15&&vwapPass&&volumePass&&geometry&&last.session==="regular"){
    if(now-barTime<=90000)status="triggered";
    else block.push("Trigger candle too old to activate");
  }else if(crossed&&!geometry)block.push("Current quote gaps beyond acceptable 2:1 entry");
  const at=new Date(now).toISOString(),result={
    id:input.ticker+"-"+(bias==="bullish"?"reclaim":"rejection")+"-"+session,
    ticker:input.ticker,setup:bias==="bullish"?"Bullish reclaim":"Bearish rejection",
    bias,basis:"daily",status,score,
    thesis:"Daily and weekly trend "+(bullish||bearish?"align":"are mixed")+". Entry requires completed-bar confirmation at intraday structure.",
    currentZone:"Quote "+(finite(q.price)?q.price:"unavailable"),
    support:"Daily 20-day average "+d20.toFixed(2)+"; recent 15-minute low "+Math.min(...recent.map(x=>x.low)).toFixed(2),
    trigger,invalidation:stop,targets,
    triggerRule:"Completed 5-minute "+(bias==="bullish"?"close above":"close below")+" "+trigger+" after the prior close was on the other side, aligned 15-minute candle, "+volumeMultiple+"× session volume and "+(bias==="bullish"?"above":"below")+" session VWAP.",
    timeframeRead:{weekly:"Close "+weekly+" vs 20-week average "+w20.toFixed(2),daily:"20/50 averages "+d20.toFixed(2)+"/"+d50.toFixed(2)+"; ATR "+range.toFixed(2),intraday:"Bar-based VWAP "+(vwap==null?"unavailable":vwap.toFixed(2))+"; volume confirmation "+(volumePass?"yes":"no")},
    quote:q,riskNote:"Underlying setup only; option contract verification pending. "+block.join("; "),
    diagnostics:{rr,atr:range,vwap,volumePass,crossed,block}
  };
  if(status==="triggered")result.activation={at,barClosedAt:last.closedAt,quoteAt:q.at,entry:q.price,feed:"realtime",evidence:"Completed reclaim/rejection with 15-minute alignment, session VWAP and relative volume"};
  return result;
}
const api={scan,bars,sma,atr,pivots};
if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.StockSetupScanner=api;
})(typeof globalThis!=="undefined"?globalThis:this);
