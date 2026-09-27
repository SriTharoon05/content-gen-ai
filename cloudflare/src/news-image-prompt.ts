/** Front-load visible subject details: small image encoders can truncate long prompts. */
export const NEWS_IMAGE_DIRECTION = `Write image_prompt as a concrete visual brief for a small image model, not a summary of the article.
Use 65–95 English words, with the essential subject and its visible shape in the FIRST 35 words. One main subject, one setting, at most two supporting objects. Name ordinary visible objects instead of abstract ideas, brand names, research jargon or metaphors.
Describe in this order: subject and distinctive silhouette/material/colour; one simple action or static pose; setting and supporting objects; camera distance and angle; lighting; composition and realistic editorial illustration style.
Make the image understandable without any headline. Specify recognizable physical features rather than “innovation”, “biological limits”, “future” or “progress”. Avoid generic glowing orbs, rainbow gradients, magical energy, collages and crowded scenes. No contradictory camera/style instructions or keyword stuffing.
Keep the main subject in the upper two-thirds, fully inside the frame with generous side margins; quiet dark lower third for text added later. Prefer a close or medium view with a simple background. Do not ask the image model to draw letters, logos, charts or exact diagrams.
Stay grounded in the source. When exact appearance is unknown, explicitly choose a representative conceptual illustration, never an allegedly exact specimen, person or photographed news event. Do not invent scientific anatomy or depict a heat-tolerant organism as literally burning.
Example visual specificity ONLY, not a required topic: “A representative amoeba seen through a microscope, one translucent irregular cell with rounded finger-like extensions, a darker central nucleus and fine granular interior, floating in clear water. Close macro view from above, soft grey-green tones, subtle laboratory lighting, gently blurred neutral background. The cell sits above centre with its entire outline visible and broad empty margins. Realistic textbook-style conceptual illustration, not an exact depiction of a newly discovered species; simple dark lower third.”`;

export function buildNewsImagePrompt(brief:string):string {
 const clean=brief.replace(/\s+/g,' ').trim();
 if(!clean||clean.length>1800)throw new Error('Invalid news image brief');
 // Keep the subject first; no model-specific weighting syntax or unsupported API fields.
 return clean+' Single coherent editorial illustration. No lettering, logos, watermark, collage or decorative border. Keep the subject clear of the bottom third.';
}
