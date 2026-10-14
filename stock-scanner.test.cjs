const assert=require("node:assert/strict");
const scan=require("./stock-scanner.cjs").scan;
const now=Date.parse("2026-10-13T15:30:00Z"),iso=t=>new Date(t).toISOString();
function seq(n,interval,end,fn,width=0.2){
 return Array.from({length:n},(_,i)=>{const close=fn(i);return {
  open:close-0.05,high:close+width,low:close-width,close,volume:100,complete:true,
  closedAt:iso(end-(n-1-i)*interval),session:"regular"}});
}
const f=seq(24,300000,now,i=>98+i*0.025);
f.at(-1).close=98.8;f.at(-1).high=99;f.at(-1).volume=200;
const d=seq(60,86400000,now-86400000,i=>90+i*0.15,1);d[30].high=110;
const fixture={ticker:"TEST",daily:d,weekly:seq(25,604800000,now-604800000,i=>90+i*0.4),
 fifteenMinute:seq(6,900000,now,i=>98+i*0.1),fiveMinute:f,
 quote:{price:98.8,at:iso(now-1000),feed:"realtime",source:"fixture",coverage:"consolidated"},
 events:{source:"fixture",checkedAt:iso(now-1000),status:"clear",nextMajorAt:null}};
const clone=()=>JSON.parse(JSON.stringify(fixture));
const valid=scan(fixture,now);
assert.equal(valid.status,"triggered");
assert.equal(valid.targets[0],110);
assert.ok(valid.activation);
let x=clone();x.quote.at=iso(now-3600000);assert.equal(scan(x,now).status,"watching");
x=clone();x.quote.at=iso(now+1000);assert.equal(scan(x,now).status,"watching");
x=clone();x.quote.feed="delayed";assert.equal(scan(x,now).status,"watching");
x=clone();x.quote.coverage="single-exchange";assert.equal(scan(x,now).status,"watching");
x=clone();x.events={};assert.equal(scan(x,now).status,"watching");
x=clone();x.events.status="scheduled";x.events.nextMajorAt=iso(now+600000);assert.equal(scan(x,now).status,"watching");
x=clone();x.daily[30].high=x.daily[30].close+1;assert.equal(scan(x,now).status,"watching");
x=clone();x.quote.price=109;assert.notEqual(scan(x,now).status,"triggered");
x=clone();x.fiveMinute.at(-1).volume=100;assert.notEqual(scan(x,now).status,"triggered");
x=clone();x.fiveMinute.at(-1).complete=false;assert.notEqual(scan(x,now).status,"triggered");
x=clone();x.fiveMinute=x.fiveMinute.slice(1);assert.equal(scan(x,now).status,"watching");
x=clone();x.daily=x.daily.slice(0,10);assert.throws(()=>scan(x,now),/Need 51 daily/);
x=clone();x.fiveMinute[1].closedAt=x.fiveMinute[0].closedAt;assert.throws(()=>scan(x,now),/unique ordered/);
x=clone();x.fiveMinute[0].high=1;assert.throws(()=>scan(x,now),/Invalid OHLCV/);
assert.equal(fixture.quote.price,98.8);
console.log("Scanner trigger, price geometry, event, candle and freshness checks passed.");
