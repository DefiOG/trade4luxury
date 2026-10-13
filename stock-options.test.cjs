const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "stock-options.js"), "utf8"), context);
const api = context.window.StockOptionsDesk;
const now = Date.parse("2026-10-13T14:00:00Z");
const item = {ticker:"TEST",bias:"bullish",basis:"daily",score:8,status:"armed",
  trigger:100,invalidation:98,targets:[104,106],triggerRule:"Completed bar reclaim",
  timeframeRead:{weekly:"up",daily:"pullback",intraday:"reclaim"},
  quote:{price:100,feed:"realtime",at:"2026-10-13T13:59:30Z",source:"test"}};
assert.equal(api.check(item,now).status,"armed");
for (const quote of [{...item.quote,at:"2026-10-13T14:00:01Z"},
  {...item.quote,at:"2026-10-12T20:00:00Z"},{...item.quote,feed:"delayed"}]) {
  assert.equal(api.check({...item,quote},now).status,"watching");
}
assert.equal(api.check({...item,score:99},now).status,"watching");
assert.equal(api.check({...item,targets:[103.9]},now).status,"watching");
assert.equal(api.check({...item,status:"triggered"},now).status,"watching");
const activated={...item,status:"triggered",activation:{
  at:"2026-10-13T13:59:00Z",barClosedAt:"2026-10-13T13:59:00Z",
  quoteAt:"2026-10-13T13:58:59Z",entry:100,feed:"realtime",evidence:"close above reclaim"}};
assert.equal(api.check(activated,now).status,"triggered");
assert.equal(api.check({...activated,activation:{...activated.activation,entry:101}},now).status,"watching");
assert.equal(api.check({...activated,activation:{...activated.activation,barClosedAt:"2026-10-13T14:01:00Z"}},now).status,"watching");
const call={type:"call",strike:100,expiration:"2026-11-20",
  verifiedAt:"2026-10-13T13:59:30Z",source:"test",underlyingPrice:100,
  bid:2,ask:2.1,volume:100,openInterest:500,iv:0.25,delta:0.5,
  catalystCheck:"No earnings before expiry"};
assert.equal(api.check({...item,optionCandidate:call},now).chainReady,true);
for(const candidate of [{...call,ask:3},{...call,type:"put"},{...call,strike:120},
  {...call,verifiedAt:"2026-10-12T20:00:00Z"},{...call,expiration:"2026-10-16"}]){
  assert.equal(api.check({...item,optionCandidate:candidate},now).chainReady,false);
}
assert.equal(api.check({...item,basis:"weekly",optionCandidate:{...call,expiration:"2026-10-30"}},now).chainReady,false);
assert.equal(api.check({...item,status:"invalidated",optionCandidate:call},now).chainReady,false);
assert.equal(api.check({...item,score:99,optionCandidate:call},now).chainReady,false);
assert.equal(api.check({...item,bias:"bearish",trigger:100,invalidation:102,targets:[96,94]},now).status,"armed");
assert.equal(api.check({...item,timeframeRead:{daily:"up"}},now).status,"watching");
assert.equal(api.check(null,now).status,"watching");
console.log("Stock/options quote, trigger, geometry and chain checks passed.");
