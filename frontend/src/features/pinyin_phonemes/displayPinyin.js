// The backend spells ü as "v" (qv, xve, lv). These turn its initial/final
// pieces back into the spelling learners see in textbooks.

const HIDDEN_U_INITIALS = new Set(['j', 'q', 'x', 'y']);

/** Full syllable from parts: ('q','v') -> 'qu', ('l','v') -> 'lü', ('','er') -> 'er'. */
export function displaySyllable(initial, final) {
  const f = HIDDEN_U_INITIALS.has(initial) ? final.replace(/^v/, 'u') : final.replace(/v/g, 'ü');
  return `${initial ?? ''}${f}`;
}

/** A final on its own, always with the visible ü: 'v' -> 'ü', 've' -> 'üe'. */
export function displayFinal(final) {
  return final.replace(/v/g, 'ü');
}
