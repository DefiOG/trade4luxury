#!/usr/bin/env node
// Personal research only. Node 20+. Provider credentials never go in exports.
import fs from "node:fs";
import {createRequire} from "node:module";
import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {StreamableHTTPClientTransport} from "@modelcontextprotocol/sdk/client/streamableHttp.js";
const require=createRequire(import.meta.url),{normalize,options,payload}=require("./robinhood-adapter.cjs"),{scan}=require("./stock-scanner.cjs");
const configPath=process.argv[2],output=process.argv[3];
if(!configPath||!output)throw Error("Usage: node robinhood-scan.mjs /private/config.json /private/scan.json");
const config=JSON.parse(fs.readFileSync(configPath,"utf8"));
const symbols=config.symbols;
if(!Array.isArray(symbols)||!symbols.length||symbols.length>10||symbols.some(s=>!/^[A-Z][A-Z0-9.-]{0,9}$/.test(s)))throw Error("Configure 1–10 stock/ETF symbols");
const token=process.env.ROBINHOOD_MCP_ACCESS_TOKEN;
if(!token)throw Error("Robinhood OAuth connection required. Authenticate through Robinhood; do not store tokens in source/config.");
const client=new Client({name:"trade-for-luxury-personal-research",version:"1.0.0"});
const transport=new StreamableHTTPClientTransport(new URL("https://agent.robinhood.com/mcp/trading"),{requestInit:{headers:{Authorization:"Bearer "+token}}});
require("./stock-options.js");
const allowed=new Set(["get_equity_quotes","get_equity_historicals","get_option_chains","get_option_instruments","get_option_quotes"]);
try{
 await client.connect(transport);
 const {tools}=await client.listTools();
 for(const name of allowed)if(!tools.some(t=>t.name===name&&t.annotations?.readOnlyHint===true))throw Error("Required read-only tool unavailable: "+name);
 async function call(name,args){
  if(!allowed.has(name))throw Error("Tool not allowed");
  const result=await client.callTool({name,arguments:args});
  if(result.isError)throw Error("Robinhood read-only request failed: "+name);
  return result;
 }
 const now=Date.now(),end=new Date(now).toISOString();
 const since=days=>new Date(now-days*86400000).toISOString();
 // Sequential, bounded requests. A throttle aborts without a retry storm.
 const raw={symbols,
  daily:await call("get_equity_historicals",{symbols,interval:"day",bounds:"regular",adjustment_type:"split",start_time:since(180),end_time:end}),
  weekly:await call("get_equity_historicals",{symbols,interval:"week",bounds:"regular",adjustment_type:"split",start_time:since(400),end_time:end}),
  fiveMinute:await call("get_equity_historicals",{symbols,interval:"5minute",bounds:"regular",adjustment_type:"split",start_time:since(5),end_time:end}),
  quotes:await call("get_equity_quotes",{symbols}),events:config.events||{},scannerConfig:config.scannerConfig||{}};
 const normalized=normalize(raw,Date.now(),{coverage:config.coverage,coverageEvidence:config.coverageEvidence});
 const ideas=[],errors=[];
 for(const snapshot of normalized.symbols){try{ideas.push(scan(snapshot,Date.now(),normalized.config))}catch(e){errors.push({ticker:snapshot.ticker,error:e.message})}}
 for(const idea of ideas){
  if(!["armed","triggered"].includes(idea.status))continue;
  try{
   const chains=payload(await call("get_option_chains",{underlying_symbol:idea.ticker})).chains||[];
   const expirations=[...new Set(chains.filter(c=>c.symbol===idea.ticker&&c.can_open_position&&Number(c.trade_value_multiplier)===100).flatMap(c=>c.expiration_dates||[]))]
    .filter(d=>{const days=(Date.parse(d+"T16:00:00-04:00")-Date.now())/86400000;return days>=14&&days<=42}).sort();
   if(!expirations.length)continue;
   const type=idea.bias==="bullish"?"call":"put";
   const instruments=await call("get_option_instruments",{chain_symbol:idea.ticker,expiration_dates:expirations[0],type,state:"active",tradability:"tradable"});
   const list=(payload(instruments).instruments||[]).filter(i=>i.chain_symbol===idea.ticker&&i.expiration_date===expirations[0]&&i.type===type&&Math.abs(Number(i.strike_price)/idea.quote.price-1)<=.05)
    .sort((a,b)=>Math.abs(Number(a.strike_price)-idea.quote.price)-Math.abs(Number(b.strike_price)-idea.quote.price)).slice(0,8);
   if(!list.length)continue;
   const quotes=await call("get_option_quotes",{instrument_ids:list.map(i=>i.id)});
   const evidence=config.events?.[idea.ticker];
   const catalyst=evidence?.source?"Earnings/major-event check: "+evidence.status+"; source "+evidence.source:"";
   const candidates=options({data:{instruments:list}},quotes,idea.ticker,idea.quote.price,catalyst);
   const suitable=candidates.find(c=>globalThis.StockOptionsDesk.check({...idea,optionCandidate:c},Date.now()).chainReady);
   if(suitable)idea.optionCandidate=suitable;
  }catch{errors.push({ticker:idea.ticker,error:"Option verification unavailable; no contract published"})}
 }
 const date=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
 const doc={date,createdAt:new Date().toISOString(),kind:"stock_options",headline:"Robinhood personal stock/options watchlist",
  summary:"Completed charts determine technical watches. Fresh quotes and verified coverage determine activation. Option contracts require separate chain checks.",ideas,errors};
 // Only public market observations and setup fields leave the adapter.
 fs.writeFileSync(output,JSON.stringify(doc,null,2)+"\n");
 console.log("Saved "+ideas.length+" personal stock setups; "+errors.length+" data gaps.");
}finally{await client.close()}
