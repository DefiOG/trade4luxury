#!/usr/bin/env node
"use strict";
const fs=require("node:fs"),{normalize}=require("./robinhood-adapter.cjs"),{scan}=require("./stock-scanner.cjs");
const [input,output,configPath]=process.argv.slice(2);
if(!input||!output)throw Error("Usage: node robinhood-import.cjs /private/capture.json /private/scan.json [/private/config.json]");
const raw=JSON.parse(fs.readFileSync(input,"utf8")),config=configPath?JSON.parse(fs.readFileSync(configPath,"utf8")):{};
const now=Date.now(),normalized=normalize(raw,now,config),ideas=[],errors=[];
for(const symbol of normalized.symbols){try{ideas.push(scan(symbol,now,normalized.config))}catch(e){errors.push({ticker:symbol.ticker,error:e.message})}}
const date=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(now));
fs.writeFileSync(output,JSON.stringify({date,createdAt:new Date(now).toISOString(),kind:"stock_options",
headline:"Robinhood personal stock/options watchlist",summary:"Sourced chart setups. Contract verification pending unless separately checked.",ideas,errors},null,2)+"\n");
console.log("Saved "+ideas.length+" setups; "+errors.length+" data gaps.");
