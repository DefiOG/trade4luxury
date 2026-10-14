#!/usr/bin/env node
/* node stock-scan.cjs --input /private/normalized-bars.json
   Optional: --output /private/export/stock_options/scans/YYYY-MM-DD-stocks.json */
"use strict";
const fs=require("node:fs"),path=require("node:path"),{scan}=require("./stock-scanner.cjs");
const args=process.argv.slice(2);
function arg(name){const i=args.indexOf(name);return i<0?null:args[i+1]}
const file=arg("--input"),out=arg("--output");
if(!file){console.error("Usage: node stock-scan.cjs --input normalized-bars.json [--output scan.json]");process.exit(1)}
const snapshot=JSON.parse(fs.readFileSync(file,"utf8"));
if(!Array.isArray(snapshot.symbols))throw Error("Input must contain symbols: [normalized symbol snapshots]");
const now=Date.now(),date=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(now));
const ideas=[],errors=[];
for(const symbol of snapshot.symbols){
 try{ideas.push(scan(symbol,now,snapshot.config||{}))}
 catch(e){errors.push({ticker:symbol.ticker||"unknown",error:e.message})}
}
const doc={date,createdAt:new Date(now).toISOString(),kind:"stock_options",
 headline:"Stock / Options technical watchlist",
 summary:"Weekly/daily trend and intraday structure determine the setup. Completed candles, fresh quotes and confirmation determine activation. Contracts require separate chain verification.",
 ideas,errors};
const json=JSON.stringify(doc,null,2);
if(out){fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,json+"\n");console.log("Wrote "+ideas.length+" stock setups; "+errors.length+" symbols missing valid data.")}
else console.log(json);
