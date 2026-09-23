import {WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep} from 'cloudflare:workers';
import type {Env} from './types';
import {triggered,triggerFailed,expire,loadTask} from './db';
import {runStage} from './stages';

export class MediaWorkflow extends WorkflowEntrypoint<Env, {taskId: string}> {
  async run(event: WorkflowEvent<{taskId: string}>, step: WorkflowStep) {
    const id = event.payload.taskId;
    // Durable events wake immediately; ten-minute DB checks recover a lost callback.
    // No media bytes or credentials are stored in Workflow step results.
    for (let round = 0; round < 25; round++) {
      const inspected=await runStage(this.env,step,`${id}-inspect-${round}`,{videoId:id,name:`inspect-${round}`,op:'media-inspect',data:{},retries:2});
      const status=inspected.status;
      if (status === 'succeeded' || status === 'failed') return {taskId:id,status};
      if (status === 'queued') {
        try {await runStage(this.env,step,`${id}-trigger-${round}`,{videoId:id,name:`trigger-${round}`,op:'media-trigger',data:{},retries:2});} catch {
          await step.do(`trigger-error-${round}`, async () => {
            await triggerFailed(this.env,id);
          });
        }
      }
      try {await step.waitForEvent(`completion-${round}`,{type:'media-complete',timeout:'10 minutes'});}
      catch { /* DB remains the authority if event delivery was lost or timed out. */ }
    }
    await step.do('expire', () => expire(this.env,id));
    return {taskId:id,status:await step.do('final-state',async ()=>(await loadTask(this.env,id)).status as string)};
  }
}
