import { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { THEME_STORAGE_KEY, WorkbenchShell } from './WorkbenchShell'

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.removeItem(THEME_STORAGE_KEY) })

function Draft() {
  const [value, setValue] = useState('')
  return <label>Draft annotation<input value={value} onChange={event => setValue(event.target.value)}/></label>
}

it('persists the keyboard-operated theme without replacing focused controls or child state', async () => {
  const user = userEvent.setup()
  const view = render(<WorkbenchShell project="FIFO"><Draft/></WorkbenchShell>)
  const input = screen.getByLabelText('Draft annotation')
  await user.type(input, 'Keep this investigation')
  const toggle = screen.getByRole('button', { name: 'Dark theme' })
  toggle.focus()
  await user.keyboard(' ')
  expect(toggle).toHaveFocus()
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
  expect(screen.getByLabelText('Draft annotation')).toBe(input)
  expect(input).toHaveValue('Keep this investigation')
  view.rerender(<WorkbenchShell project="Saved FIFO" campaign="saved-campaign"><Draft/></WorkbenchShell>)
  expect(screen.getByLabelText('Draft annotation')).toBe(input)
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
  expect(input).toHaveValue('Keep this investigation')
  view.unmount()
  render(<WorkbenchShell project="FIFO"><Draft/></WorkbenchShell>)
  expect(screen.getByRole('button', { name: 'Dark theme' })).toHaveAttribute('aria-pressed', 'true')
})

it('defaults invalid stored values to light and allows an in-memory theme when storage fails', async () => {
  localStorage.setItem(THEME_STORAGE_KEY, 'unsupported-value')
  const view = render(<WorkbenchShell project="FIFO"><Draft/></WorkbenchShell>)
  expect(screen.getByRole('button', { name: 'Dark theme' })).toHaveAttribute('aria-pressed', 'false')
  view.unmount()
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage unavailable') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage unavailable') })
  render(<WorkbenchShell project="FIFO"><Draft/></WorkbenchShell>)
  const toggle = screen.getByRole('button', { name: 'Dark theme' })
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await userEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
})
