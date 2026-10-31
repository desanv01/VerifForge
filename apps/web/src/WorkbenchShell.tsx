import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export const THEME_STORAGE_KEY = 'verifforge.theme.v1'
type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try { return localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light' }
  catch { return 'light' }
}

/** Presentation only. Theme never changes the identity of the application subtree. */
export function WorkbenchShell({ project, campaign, children }: {
  project: string
  campaign?: string
  children: ReactNode
}) {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  useEffect(() => {
    try { localStorage.setItem(THEME_STORAGE_KEY, theme) } catch { /* Storage is optional. */ }
  }, [theme])

  return <div className="app-shell" data-theme={theme}>
    <a className="skip-link" href="#main">Skip to investigation</a>
    <aside className="sidebar" aria-label="Workspace navigation">
      <a className="brand" href="/">VerifForge</a>
      <span className="local-badge">Local workspace</span>
      <div className="sidebar-project"><span className="workspace-label">PROJECT</span><a href="#experiment">{project}</a></div>
      <nav aria-label="Workspace">
        <span className="workspace-label">INVESTIGATIONS</span>
        <a className="nav-active" href="#main">Investigation workbench</a>
        <a href="#experiment">Review experiment</a>
        <a href="#evidence">Finding &amp; evidence</a>
        <a href="#recorded-evidence">Recorded investigations</a>
        <a href="#planner">Bounded planner</a>
      </nav>
      <div className="sidebar-note"><p>Curated RTL &amp; recorded evidence</p><p>Support follows the local catalog. Execution and design verdicts are separate.</p></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <nav className="breadcrumb" aria-label="Breadcrumb"><a href="#experiment">{project}</a><span aria-hidden="true">›</span><span>{campaign ? `Campaign ${campaign}` : 'Engineering evidence'}</span></nav>
        <button className="theme-toggle" type="button" aria-pressed={theme === 'dark'} onClick={() => setTheme(current => current === 'light' ? 'dark' : 'light')}><span className="theme-swatch" aria-hidden="true"/> Dark theme</button>
      </header>
      <main id="main" tabIndex={-1}>{children}</main>
    </div>
  </div>
}
