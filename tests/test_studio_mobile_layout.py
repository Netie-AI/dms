"""STUDIO-MOBILE-01 (#171) + STUDIO-MOBILE-02 (#182): phone-width chrome.

CI does not run vitest. These read the Studio/Chat layout so a revert of the
Sources drawer, Operate LeftNav drawer, or TopBar clip shows up on the same
pytest job as the rest of the suite.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "apps" / "ui" / "src"


def test_chat_source_panel_is_overlay_below_lg_not_a_docked_22rem_sibling() -> None:
    src = (UI / "components" / "SourcePanel.tsx").read_text(encoding="utf-8")
    assert "max-lg:fixed" in src
    assert "w-[22rem]" in src
    assert "max-lg:w-[min(22rem,calc(100%-2.75rem))]" in src
    assert 'data-testid="source-panel-open"' in src
    assert "min-h-11" in src
    shell = (UI / "components" / "AppShell.tsx").read_text(encoding="utf-8")
    assert "source-panel-slot" in shell
    assert "max-lg:w-0" in shell


def test_chat_sources_start_closed_below_lg_and_do_not_auto_open_after_ask() -> None:
    ctx = (UI / "context" / "AppContext.tsx").read_text(encoding="utf-8")
    assert "sourcesStartOpen" in ctx
    assert "shouldExpandSourcesDock" in ctx
    assert "setSourcePanelOpen(true)" in ctx


def test_studio_keeps_desktop_two_column_and_collapses_files_and_sqlsrc() -> None:
    page = (UI / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    assert "lg:grid-cols-[22rem_1fr]" in page
    assert "max-lg:hidden" in page
    assert "studio-sources-toggle" in page
    assert "<SqlSourcePanel " in page


def test_composer_sets_ink_placeholder_and_caret() -> None:
    chat = (UI / "pages" / "ChatPage.tsx").read_text(encoding="utf-8")
    assert "text-[var(--color-ink)]" in chat
    assert "placeholder:text-[var(--color-ink-muted)]" in chat
    assert "caret-[var(--color-ink)]" in chat


def test_topbar_short_labels_below_lg_keep_desktop_wording() -> None:
    bar = (UI / "components" / "TopBar.tsx").read_text(encoding="utf-8")
    assert 'className="lg:hidden">New</span>' in bar
    assert 'className="hidden lg:inline">+ New</span>' in bar
    assert 'className="lg:hidden">Spaces</span>' in bar
    assert 'className="hidden lg:inline">Manage</span>' in bar
    assert 'className="lg:hidden">Operate</span>' in bar
    assert 'className="hidden lg:inline">Switch to Operate</span>' in bar


def test_leftnav_is_drawer_below_lg_not_a_permanent_w52_sibling() -> None:
    nav = (UI / "components" / "LeftNav.tsx").read_text(encoding="utf-8")
    assert "max-lg:fixed" in nav
    assert "max-lg:hidden" in nav
    assert 'data-testid="left-nav-backdrop"' in nav
    assert "w-52" in nav
    shell = (UI / "components" / "AppShell.tsx").read_text(encoding="utf-8")
    assert "left-nav-slot" in shell
    assert "max-lg:w-0" in shell


def test_operate_nav_starts_collapsed_below_lg() -> None:
    vp = (UI / "lib" / "viewport.ts").read_text(encoding="utf-8")
    assert "navStartsCollapsed" in vp
    ctx = (UI / "context" / "AppContext.tsx").read_text(encoding="utf-8")
    assert "navStartsCollapsed" in ctx
    assert "closeNavDrawer" in ctx
    assert "setNavCollapsed(mode === \"cream\")" not in ctx


def test_topbar_does_not_clip_primary_actions_below_lg() -> None:
    bar = (UI / "components" / "TopBar.tsx").read_text(encoding="utf-8")
    assert "overflow-x-hidden" not in bar
    assert "overflow-x-auto" in bar
    assert 'data-testid="topbar-title"' in bar
    assert "hidden h-8 shrink-0" in bar
    assert "lg:flex" in bar
    assert "lg:inline" in bar

