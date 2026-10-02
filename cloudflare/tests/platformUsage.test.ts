import {test} from 'node:test';
import assert from 'node:assert/strict';
import {platformUsage} from '../src/platformUsage';
import type {Env} from '../src/types';

test('platform diagnostics whitelist numbers and never expose credentials',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>Response.json({plan:'Free',api_key:'secret',credits:{usage:.85,limit:25,secret:'hidden'},storage:{usage:446692758},resources:233});
  try{
    assert.deepEqual(await platformUsage({CLOUDINARY_CLOUD_NAME:'fixture',CLOUDINARY_API_KEY:'key',CLOUDINARY_API_SECRET:'secret'} as Env),{cloudinary:{available:true,plan:'Free',credits:{usage:.85,limit:25},storage:{usage:446692758},resources:233}});
  }finally{globalThis.fetch=original;}
});

test('unavailable usage endpoint returns safe status rather than provider response',async()=>{
  const original=globalThis.fetch;globalThis.fetch=async()=>new Response('sensitive',{status:403});
  try{assert.deepEqual(await platformUsage({} as Env),{cloudinary:{available:false,status:403}});}
  finally{globalThis.fetch=original;}
});
