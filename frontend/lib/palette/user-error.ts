/** Errors saved before the backend sanitised them can carry provider names,
 * API URLs or key names; never show those to users (details stay in server logs). */
const INTERNAL = /https?:\/\/|\b[A-Z][A-Z0-9_]*_KEY\b|\b(?:gpt|claude_\w+|gemini|palette|fal|runware|router|midjourney|elevenlabs|ffmpeg):/i;

export function userError(text: string) {
  return INTERNAL.test(text) ? 'Something went wrong. Please try again.' : text;
}
