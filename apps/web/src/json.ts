// This is a parsed inspection view, not a raw-JSON serializer. Numeric lexemes
// outside JavaScript's safe integer range are retained as strings before parsing.
export const MAX_JSON_CHARACTERS = 16 * 1024 * 1024

export function parseInspectionJson(source: string): unknown {
  if (source.length > MAX_JSON_CHARACTERS) throw new RangeError('JSON exceeds the inspection size limit.')
  // Validate the ORIGINAL grammar first: quoting a numeric token must never make
  // an invalid numeric object key valid. Discard these rounded numeric values;
  // preservation below always reads the unchanged source, never this object.
  const ordinary: unknown = JSON.parse(source)
  const numeric = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y
  const pieces: string[] = []
  let cursor = 0
  let copiedThrough = 0
  while (cursor < source.length) {
    if (source[cursor] === '"') {
      // Skip whole JSON strings, including escaped quotes/backslashes. JSON.parse
      // below remains the grammar authority, including invalid escapes/control chars.
      cursor++
      while (cursor < source.length) {
        const character = source[cursor++]
        if (character === '\\') cursor++
        else if (character === '"') break
      }
      continue
    }
    const character = source[cursor]
    if (character === '-' || (character >= '0' && character <= '9')) {
      numeric.lastIndex = cursor
      const match = numeric.exec(source)
      if (match) {
        const token = match[0]
        const value = Number(token)
        if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
          pieces.push(source.slice(copiedThrough, cursor), JSON.stringify(token))
          copiedThrough = cursor + token.length
        }
        cursor += token.length
        continue
      }
    }
    cursor++
  }
  if (pieces.length === 0) return ordinary
  pieces.push(source.slice(copiedThrough))
  return JSON.parse(pieces.join('')) as unknown
}

export function stringifySubmission(value: unknown): string {
  const encoded = JSON.stringify(value, (_key, field: unknown) => {
    if (typeof field === 'bigint' || (typeof field === 'number' && (!Number.isFinite(field) || (Number.isInteger(field) && !Number.isSafeInteger(field))))) {
      throw new RangeError('Submission contains an unsafe numeric value. Use the supported bounded inputs; saved inspection values cannot be resubmitted as numbers.')
    }
    return field
  })
  if (encoded === undefined) throw new TypeError('Submission is not a JSON value.')
  return encoded
}
