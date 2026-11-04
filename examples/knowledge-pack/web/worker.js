"use strict";
importScripts("wasm_exec.js");
const ready=(async()=>{
 const go=new Go();const response=await fetch("verifier.wasm");if(!response.ok)throw Error("Verifier download failed");
 const {instance}=await WebAssembly.instantiate(await response.arrayBuffer(),go.importObject);
 go.run(instance).catch(e=>postMessage({fatal:String(e)}));
 if(typeof self.axmVerify!=="function")throw Error("Verifier did not initialize");
 postMessage({ready:true});
})();
ready.catch(e=>postMessage({fatal:String(e)}));
self.onmessage=async({data:m})=>{
 try{await ready;
  if(!m||typeof m.archive!=="string"||typeof m.trusted_key!=="string"||m.archive.length>12*1024*1024||m.trusted_key.length>1792)throw Error("Input limit exceeded");
  const value=JSON.parse(self.axmVerify(m.operation,m.archive,m.trusted_key,m.mode||""));
  postMessage({id:m.id,value});
 }catch(e){postMessage({id:m?.id,error:String(e)});}
};
