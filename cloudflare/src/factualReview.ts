/** A model reviewer cannot silently approve factual risks flagged by the writer. */
export function enforceFactualReview(script:any,qa:any){
  const flagged=Array.isArray(script?.flagged_claims)?script.flagged_claims.filter((value:any)=>typeof value==='string'&&value.trim()).map((value:string)=>value.trim()):[];
  if(!flagged.length)return qa;
  const repairs=flagged.map((claim:string)=>`Unresolved factual risk flagged by the writer: ${claim}. Remove the unsupported assertion, or explicitly frame it as a not-currently-available thought experiment/speculation. Do not invent dates, statistics, quantitative comparisons or technical confirmation. Clear this flag only after the narration is corrected.`);
  return {...qa,passed:false,findings:[...new Set([...(qa.findings||[]),...repairs])],reasoning:[qa.reasoning||'','Unresolved writer-flagged claims cannot pass editorial QA.'].filter(Boolean).join(' ')};
}
