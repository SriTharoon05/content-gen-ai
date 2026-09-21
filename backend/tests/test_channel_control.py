import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from pydantic import ValidationError
from app import social,seed,api
from app.agents.roles import channel_instructions
from app.captions import write_ass

class ChannelControlTests(unittest.TestCase):
    def test_safe_channel_identifiers_and_required_fields(self):
        for slug in ['../test','a/b','UPPER','', 'a'*65]:
            with self.assertRaises(ValidationError):
                api.ChannelCreate(slug=slug,name='Example',niche='Stories')
        with self.assertRaises(ValidationError):
            api.ChannelCreate(slug='example',name=' ',niche='Stories')

    def test_create_channel_stores_custom_profile(self):
        db=MagicMock(); db.__enter__.return_value.get.return_value=None
        with patch.object(api,'session_scope',return_value=db):
            result=api.create_channel(api.ChannelCreate(slug='new-channel',name='New Channel',niche='Gardening',instructions='Warm practical storytelling',topic_seeds=['Balcony gardens']))
        self.assertEqual(result['channel']['instructions'],'Warm practical storytelling')
        self.assertEqual(result['channel']['strategy']['topic_seeds'],['Balcony gardens'])

    def test_custom_instructions_replace_brand_defaults(self):
        self.assertEqual(channel_instructions({'slug':'lorehush','strategy_json':{'instructions':'My own instructions'}}),'My own instructions')

    def test_seed_preserves_user_edits(self):
        row=SimpleNamespace(name='Edited',niche='Edited niche',tagline='Mine',strategy_json={'instructions':'Saved'})
        db=MagicMock();db.__enter__.return_value.get.return_value=row
        with patch.object(seed,'session_scope',return_value=db),patch.object(seed,'load'):
            seed.seed_all()
        self.assertEqual((row.name,row.niche,row.tagline),('Edited','Edited niche','Mine'))
        self.assertEqual(row.strategy_json['instructions'],'Saved')

    def test_per_run_publishing_overrides_global_but_not_edit_review(self):
        with patch.object(social,'cfg',return_value=True):
            self.assertFalse(social.review_required(SimpleNamespace(options_json={'publishing_mode':'direct'})))
            self.assertTrue(social.review_required(SimpleNamespace(options_json={'publishing_mode':'direct','force_review':True})))
        with patch.object(social,'cfg',return_value=False):
            self.assertTrue(social.review_required(SimpleNamespace(options_json={'publishing_mode':'review'})))

    def test_direct_run_routes_even_when_global_automation_off(self):
        row=SimpleNamespace(options_json={'publishing_mode':'direct'},state='READY',output_path='video.mp4',approved=False,channel_slug='new')
        db=MagicMock();db.__enter__.return_value.get.return_value=row
        with patch.object(social,'session_scope',return_value=db),patch.object(social,'cfg',return_value=False),patch.object(social,'status',return_value={'youtube':True}),patch.object(social,'request_publish',return_value={'status':'pending'}) as publish:
            self.assertEqual(social.route_upload('v')['status'],'pending')
            publish.assert_called_once()

    def test_empty_subtitle_file_remains_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'captions.ass'
            stats=write_ass([],[],set(),target,60)
            self.assertEqual(stats['dialogue_lines'],0)
            self.assertNotIn('Dialogue:',target.read_text(encoding='utf-8'))
