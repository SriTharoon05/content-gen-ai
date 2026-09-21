import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from app import story_selection, content_ledger, pipeline
from app.agents import roles


class DirectContentTests(unittest.TestCase):
    def test_direct_ideas_keep_uniqueness_without_external_search(self):
        channel={'slug':'curiousduo','name':'CuriousDuo','niche':'conversation',
                 'strategy_json':{'conversation':True,'ideation_route':'frontier'}}
        candidates=SimpleNamespace(candidates=[SimpleNamespace(core_concept='An original conversation')])
        reservation={'collision':False,'id':'r','core_entity':'nostalgia','content_angle':'opinion','core_concept':'An original conversation'}
        with patch.object(story_selection,'generate_model',return_value=candidates), \
             patch.object(story_selection,'fetch_wikimedia_daily_seed',side_effect=AssertionError('external check')), \
             patch.object(story_selection,'frontier_node',side_effect=AssertionError('external check')), \
             patch.object(story_selection,'search_provider',side_effect=AssertionError('external check')), \
             patch.object(content_ledger,'embed_text',return_value=[0.0]*768), \
             patch.object(content_ledger,'_try_reserve',return_value=reservation) as reserve:
            self.assertEqual(story_selection.select_unique_concept(channel,'v')['id'],'r')
            reserve.assert_called_once()

    def test_existing_failed_reservation_reaches_writer_without_wikipedia(self):
        db=MagicMock(); db.__enter__.return_value.get.return_value=SimpleNamespace(topic='nostalgia',script_json={},premise_json={},qa_json={},options_json={})
        channel={'slug':'curiousduo','strategy_json':{}}
        with patch.object(pipeline,'session_scope',return_value=db),patch.object(pipeline,'work_dir'), \
             patch.object(pipeline,'set_state'),patch.object(pipeline,'record'), \
             patch.object(pipeline.registry,'prior_stories',return_value=[]), \
             patch.object(pipeline.content_ledger,'reserve_for_video',return_value={'core_concept':'Discuss old games'}), \
             patch.object(story_selection,'source_evidence',side_effect=AssertionError('Wikipedia called')), \
             patch.object(roles,'write_premise',side_effect=RuntimeError('writer reached')) as writer:
            with self.assertRaisesRegex(RuntimeError,'writer reached'):
                pipeline.stage_story('v',channel,{})
            self.assertIn('not externally verified',writer.call_args.args[1])

    def test_backlog_does_not_call_external_providers(self):
        with patch.object(story_selection,'search_provider',side_effect=AssertionError('external check')):
            self.assertEqual(story_selection.retry_backlog()['status'],'disabled')

    def test_prompt_does_not_require_source_verification(self):
        channel={'slug':'curiousduo','name':'CuriousDuo','tagline':'Two friends','niche':'conversation','strategy_json':{}}
        with patch.object(roles,'brand',return_value=''),patch.object(roles.memory,'prompt_block',return_value=''):
            prompt=roles.context(channel,{'source_evidence':'STALE EVIDENCE'})
        self.assertIn('external fact-checking is disabled',prompt)
        self.assertNotIn('STALE EVIDENCE',prompt)

