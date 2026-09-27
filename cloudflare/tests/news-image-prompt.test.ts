import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildNewsImagePrompt,NEWS_IMAGE_DIRECTION} from '../src/news-image-prompt';

test('news illustration retains subject first and appends simple layout exclusions',()=>{
 const result=buildNewsImagePrompt('  A translucent irregular cell.\n Soft laboratory lighting. ');
 assert.ok(result.startsWith('A translucent irregular cell. Soft laboratory lighting.'));
 assert.ok(result.includes('No lettering, logos, watermark'));
 assert.ok(result.includes('bottom third'));
});
test('news illustration rejects empty or oversized briefs',()=>{
 assert.throws(()=>buildNewsImagePrompt('  '));
 assert.throws(()=>buildNewsImagePrompt('x'.repeat(1801)));
});
test('small-model direction prioritizes concrete subjects and avoids fabricated evidence',()=>{
 for(const text of ['FIRST 35 words','65–95','One main subject','not an exact depiction','not a required topic','literally burning'])assert.ok(NEWS_IMAGE_DIRECTION.includes(text));
});
