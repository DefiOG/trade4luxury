"use strict";
/* Normalize read-only Robinhood Trading MCP responses. Re-check against tools/list
   on the authenticated server; schema capture is not a live entitlement test. */
const minute=60000;
function payload(x){
 if(x?.isError)throw Error("Robinhood tool returned an error");
 if(x?.structuredContent)return x.structuredContent.data||x.structuredContent;
 if(x?.data)return x.data;
 if(Array.isArray(x?.content)){
  for(const item of x.content)if(item.type==="text"){try{const j=JSON.parse(item.text);return j.data||j}catch{}}
 }
 throw Error("Unrecognized Robinhood response envelope");
}
function number(x){if(x===null||x===""||x===undefined)throw Error("Missing market value");const v=Number(x);if(!Number.isFinite(v))throw Error("Non-numeric market value");return v}
function time(x){if(typeof x!=="string"||!/(Z|[+-]\d\d:\d\d)$/.test(x)||!Number.isFinite(Date.parse(x)))throw Error("Quote/candle timestamp requires timezone");return Date.parse(x)}
function wallUTC(date,hour,minuteValue=0){
 const [year,month,day]=date.split("-").map(Number);
 let guess=Date.UTC(year,month-1,day,hour,minuteValue);
 const fmt=new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
 for(let n=0;n<3;n++){
  const p=Object.fromEntries(fmt.formatToParts(new Date(guess)).filter(x=>x.type!=="literal").map(x=>[x.type,+x.value]));
  const wall=Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute);
  guess+=Date.UTC(year,month-1,day,hour,minuteValue)-wall;
 }
 return guess;
}
function historical(response,ticker,interval,now){
 const rows=payload(response).results;
 if(!Array.isArray(rows))throw Error("Historicals results missing");
 const item=rows.find(r=>r.symbol===ticker&&r.interval===interval);
 if(!item||!Array.isArray(item.bars)||item.bounds!=="regular")throw Error("Missing requested regular-session history for "+ticker+" "+interval);
 const out=[];
 for(const b of item.bars){
  if(!b||b.interpolated===true)continue;
  const start=time(b.begins_at),date=b.begins_at.slice(0,10);
  let end;
  if(interval==="5minute"){if(b.session!=="reg")continue;end=start+5*minute}
  else if(interval==="day")end=wallUTC(date,16);
  else if(interval==="week"){
   const anchor=new Date(date+"T12:00:00Z");
   const offset=(5-anchor.getUTCDay()+7)%7;
   anchor.setUTCDate(anchor.getUTCDate()+offset);
   end=wallUTC(anchor.toISOString().slice(0,10),16);
  }else throw Error("Unsupported interval");
  if(end>now)continue;
  out.push({open:number(b.open_price),high:number(b.high_price),low:number(b.low_price),close:number(b.close_price),volume:number(b.volume),
   closedAt:new Date(end).toISOString(),complete:true,session:"regular"});
 }
 return out.sort((a,b)=>Date.parse(a.closedAt)-Date.parse(b.closedAt));
}
function fifteen(five){
 const buckets=new Map();
 const fmt=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
 for(const b of five){
  const p=Object.fromEntries(fmt.formatToParts(new Date(time(b.closedAt)-5*minute)).filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
  const offset=Number(p.hour)*60+Number(p.minute)-570;
  if(offset<0||offset>=390)continue;
  const bucket=Math.floor(offset/15),key=p.year+"-"+p.month+"-"+p.day+":"+bucket;
  const rows=buckets.get(key)||[];rows.push(b);buckets.set(key,rows);
 }
 return [...buckets.values()].filter(rows=>rows.length===3&&time(rows[1].closedAt)-time(rows[0].closedAt)===5*minute&&time(rows[2].closedAt)-time(rows[1].closedAt)===5*minute).map(rows=>({
  open:rows[0].open,high:Math.max(...rows.map(b=>b.high)),low:Math.min(...rows.map(b=>b.low)),
  close:rows[2].close,volume:rows.reduce((a,b)=>a+b.volume,0),closedAt:rows[2].closedAt,complete:true,session:"regular"
 }));
}
function quote(response,ticker,config={}){
 const rows=payload(response).results;
 if(!Array.isArray(rows))throw Error("Equity quote results missing");
 const q=rows.find(r=>r.quote?.symbol===ticker)?.quote;
 if(!q||q.state!=="active"||q.has_traded!==true)throw Error("Inactive or unavailable quote");
 time(q.venue_last_trade_time);
 // Regular-session scanner deliberately uses regular-session trade timestamps.
 // Never replace the market timestamp with the time the request was made.
 return {price:number(q.last_trade_price),at:q.venue_last_trade_time,source:"Robinhood Trading MCP",
  feed:"realtime",coverage:config.coverage==="consolidated"&&config.coverageEvidence?"consolidated":"unknown"};
}
function normalize(raw,now=Date.now(),config={}){
 if(!Array.isArray(raw.symbols))throw Error("symbols list missing");
 return {symbols:raw.symbols.map(ticker=>{
  const five=historical(raw.fiveMinute,ticker,"5minute",now);
  return {ticker,daily:historical(raw.daily,ticker,"day",now),weekly:historical(raw.weekly,ticker,"week",now),
   fiveMinute:five,fifteenMinute:fifteen(five),quote:quote(raw.quotes,ticker,config),events:raw.events?.[ticker]||{}};
 }),config:raw.scannerConfig||{}};
}
function options(instrumentsResponse,quotesResponse,ticker,underlyingPrice,catalystCheck){
 const instruments=payload(instrumentsResponse).instruments||[],quotes=payload(quotesResponse).results||[];
 return instruments.filter(i=>i.chain_symbol===ticker&&i.state==="active"&&i.tradability==="tradable"&&number(i.trade_value_multiplier)===100).flatMap(i=>{
  const q=quotes.find(r=>r.quote?.instrument_id===i.id)?.quote;if(!q)return [];
  try{time(q.updated_at);return [{type:i.type,strike:number(i.strike_price),expiration:i.expiration_date,
   underlyingPrice,bid:number(q.bid_price),ask:number(q.ask_price),volume:number(q.volume),openInterest:number(q.open_interest),
   iv:number(q.implied_volatility),delta:number(q.delta),source:"Robinhood Trading MCP",verifiedAt:q.updated_at,catalystCheck}]}catch{return []}
 });
}
module.exports={payload,historical,fifteen,quote,normalize,options,wallUTC};
