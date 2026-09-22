"""Render-only API trigger. Never place credentials or content in pipeline parameters."""
import httpx
from .config import boot


def trigger(task_id):
    cfg = boot()
    if not all((cfg.circleci_token, cfg.circleci_project_slug, cfg.circleci_pipeline_definition_id)):
        raise ValueError('Configure CIRCLECI_TOKEN, CIRCLECI_PROJECT_SLUG and CIRCLECI_PIPELINE_DEFINITION_ID')
    response = httpx.post(f'https://circleci.com/api/v2/project/{cfg.circleci_project_slug}/pipeline/run',
        headers={'Circle-Token': cfg.circleci_token}, timeout=30,
        json={'definition_id': cfg.circleci_pipeline_definition_id,
              'config': {'branch': cfg.circleci_branch}, 'checkout': {'branch': cfg.circleci_branch},
              'parameters': {'render_task_id': task_id}})
    # Do not log provider bodies/headers, which can contain request details.
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f'CircleCI trigger HTTP {response.status_code}')
    pipeline_id = response.json().get('id')
    if not pipeline_id:
        raise RuntimeError('CircleCI created no pipeline; check branch/config and remove skip-CI commit markers')
    return pipeline_id
