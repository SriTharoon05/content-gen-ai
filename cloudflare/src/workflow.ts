import {WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep} from 'cloudflare:workers';
import type {Env} from './types';
import {triggered,triggerFailed,expire,loadTask} from './db';

export class MediaWorkflow extends WorkflowEntrypoint<Env, {taskId: string}> {
  async run(event: WorkflowEvent<{taskId: string}>, step: WorkflowStep) {
    const id = event.payload.taskId;
    // Durable events wake immediately; ten-minute DB checks recover a lost callback.
    // No media bytes or credentials are stored in Workflow step results.
    for (let round = 0; round < 25; round++) {
      const status = await step.do(`inspect-${round}`, async () => {
        await expire(this.env,id);
        const task = await loadTask(this.env,id);
        return task.status as string;
      });
      if (status === 'succeeded' || status === 'failed') return {taskId:id,status};
      if (status === 'queued') {
        try {await step.do(`trigger-${round}`, {retries:{limit:2,delay:'30 seconds',backoff:'exponential'}}, async () => {
          const r = await fetch(`https://circleci.com/api/v2/project/${this.env.CIRCLECI_PROJECT_SLUG}/pipeline/run`, {
            method: 'POST', headers: {'Circle-Token':this.env.CIRCLECI_TOKEN,'Content-Type':'application/json'},
            body: JSON.stringify({definition_id:this.env.CIRCLECI_PIPELINE_DEFINITION_ID,
              config:{branch:this.env.CIRCLECI_BRANCH},checkout:{branch:this.env.CIRCLECI_BRANCH},parameters:{render_task_id:id}}),
          });
          if (!r.ok) throw new Error(`CircleCI trigger HTTP ${r.status}`);
          const body = await r.json() as {id?:string};
          if (!body.id) throw new Error('CircleCI returned no pipeline ID');
          await triggered(this.env,id,body.id);
          return body.id;
        });} catch {
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
