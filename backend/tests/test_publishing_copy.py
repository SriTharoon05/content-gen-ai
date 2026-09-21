import unittest
from types import SimpleNamespace
from app.publishing_copy import clean_hashtags, publication_copy


class PublishingCopyTests(unittest.TestCase):
    def video(self, **changes):
        return SimpleNamespace(**dict({'title':'Could you type with your brain? [flux.1-schnell]',
            'description':'What research can and cannot do. #Brain #viral',
            'instagram_caption':'What would you try first? #Brain',
            'hashtags':['#Brain','brain','Neuroscience','Technology','BCI','Science','extra','viral'],
            'options_json':{'image_model':'black-forest-labs/flux.1-schnell'}}, **changes))

    def test_public_title_hides_model_and_keeps_legitimate_brackets(self):
        self.assertEqual(publication_copy(self.video())['youtube_title'], 'Could you type with your brain?')
        self.assertEqual(publication_copy(self.video(title='A mystery [Part 2]'))['youtube_title'], 'A mystery [Part 2]')

    def test_visible_hashtags_are_deduplicated_and_bounded(self):
        copy = publication_copy(self.video())
        self.assertEqual(copy['youtube_description'].count('#'),3)
        self.assertEqual(copy['instagram_caption'].count('#'),5)
        self.assertEqual(copy['instagram_caption'].count('#Brain'),1)
        self.assertNotIn('#viral',copy['youtube_description'])
        self.assertEqual(len(copy['youtube_tags']),5)

    def test_legacy_long_copy_keeps_hashtag_footer_inside_limits(self):
        copy = publication_copy(self.video(title='T'*120,description='D'*6000,instagram_caption='C'*3000))
        self.assertLessEqual(len(copy['youtube_title']),100)
        self.assertLessEqual(len(copy['youtube_description']),5000)
        self.assertLessEqual(len(copy['instagram_caption']),2200)
        self.assertTrue(copy['instagram_caption'].endswith('#Science'))

    def test_tag_cleanup_preserves_localized_topics(self):
        self.assertEqual(clean_hashtags(['#தமிழ்','தமிழ்','fyp','123','Brain science']),['தமிழ்','Brainscience'])
