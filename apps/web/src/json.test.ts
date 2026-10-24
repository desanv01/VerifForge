import { afterEach, describe, expect, it, vi } from 'vitest'
import { mutate, request } from './api'
import { MAX_JSON_CHARACTERS, parseInspectionJson, stringifySubmission } from './json'

afterEach(() => vi.restoreAllMocks())

describe('precision-safe inspection JSON', () => {
  it('preserves unsafe positive, negative and max uint64 integer lexemes', () => {
    expect(parseInspectionJson('{"value":9007199254740993,"negative":-9007199254740993,"max":18446744073709551615}')).toEqual({
      value:'9007199254740993', negative:'-9007199254740993', max:'18446744073709551615',
    })
  })
  it('handles nested values and integral exponent/decimal forms without rounding their lexemes', () => {
    expect(parseInspectionJson('[{"nested":[9007199254740993,{"n":-18446744073709551615}]} , 9.007199254740993e15, 18446744073709551615.0, 1e400]')).toEqual([
      {nested:['9007199254740993',{n:'-18446744073709551615'}]},'9.007199254740993e15','18446744073709551615.0','1e400',
    ])
  })
  it('leaves safe boundary integers, ordinary floats, strings, escapes and keys unchanged', () => {
    const ordinary = { value:9007199254740991, negative:-9007199254740991, zero:0, float:1.25, exponent:2e3, bool:true, empty:null, text:'9007199254740993', escaped:'quote: "9007199254740993"; backslash: \\; braces: [1,2]', '18446744073709551615':'a number-like key' }
    expect(parseInspectionJson(JSON.stringify(ordinary))).toEqual(ordinary)
    expect(Object.is(parseInspectionJson('-0'),-0)).toBe(true)
  })
  it('handles even and odd escaped-backslash sequences around unsafe numbers', () => {
    const expected = { trailing:'\\', quotation:'\\"', nested:{text:'"18446744073709551615"', value:'wide-placeholder'} }
    const raw = JSON.stringify(expected).replace('"wide-placeholder"','18446744073709551615')
    expect(parseInspectionJson(raw)).toEqual({...expected,nested:{...expected.nested,value:'18446744073709551615'}})
  })
  it.each(['{9007199254740993:1}', '{"x":01}', '{"x":9007199254740993junk}', '{"x":-}', '[1e]', '{"x":"unterminated}', '[9007199254740993,]'])('rejects original malformed JSON %s', source => {
    expect(() => parseInspectionJson(source)).toThrow(SyntaxError)
  })
  it('bounds inspection parsing before grammar validation', () => {
    expect(() => parseInspectionJson(' '.repeat(MAX_JSON_CHARACTERS + 1))).toThrow(RangeError)
  })
  it('decodes raw HTTP response text instead of the rounded Response.json value', async () => {
    vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{"plan":{"operations":[{"op":"write","value":18446744073709551615}]},"trace":[{"expected_data":9007199254740993,"observed_data":9007199254740992}]}'))
    const view = await request<{plan:unknown;trace:{expected_data:string;observed_data:string}[]}>('/campaigns/wide')
    expect(JSON.stringify(view.plan)).toContain('"18446744073709551615"')
    expect(view.trace[0].expected_data).toBe('9007199254740993')
    expect(view.trace[0].observed_data).toBe('9007199254740992')
    expect(view.trace[0].expected_data).not.toBe(view.trace[0].observed_data)
  })
})

describe('outbound numeric boundary', () => {
  it.each([9007199254740992,-9007199254740992,Number('18446744073709551615'),NaN,Infinity,-Infinity,1n])('rejects unsafe/non-finite numbers and bigint %s', value => {
    const fetch = vi.spyOn(globalThis,'fetch')
    expect(() => mutate('/campaigns',{plan:{operations:[{op:'write',value}]}})).toThrow(/unsafe numeric value/)
    expect(fetch).not.toHaveBeenCalled()
  })
  it('serializes normal numbers and string literals without coercion', () => {
    const payload = {seed:4294967295,budget:{wall_time_seconds:120},value:42,text:'9007199254740993'}
    expect(stringifySubmission(payload)).toBe(JSON.stringify(payload))
  })
  it('keeps replay body absent and uses the server record', async () => {
    const fetch = vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{"id":"replay","execution_status":"queued","verdict":null}'))
    await mutate('/attempts/db-id/replay',undefined,'stable-key')
    expect(fetch).toHaveBeenCalledWith('/api/attempts/db-id/replay',expect.objectContaining({method:'POST',body:undefined,headers:expect.objectContaining({'Idempotency-Key':'stable-key'})}))
  })
})
