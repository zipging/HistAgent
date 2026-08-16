#!/usr/bin/env python3
"""Render the public HistAgent tutorial pages from their executed notebooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from nbconvert import HTMLExporter


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_DIR = ROOT / "docs" / "notebooks"
TUTORIAL_DIR = ROOT / "docs" / "tutorials"
ASSET_VERSION = "20260816academic1"


@dataclass(frozen=True)
class Tutorial:
    number: str
    slug: str
    notebook: str
    title: str
    summary: str


TUTORIALS = (
    Tutorial("01", "ranked-molecular-readouts", "ranked_molecular_readouts.ipynb", "Ranked molecular readouts", "HitRate@50 · mAP@50 · PCC"),
    Tutorial("02", "spatial-biological-findings", "spatial_biological_findings.ipynb", "Spatial biological findings", "Expression · TLS · evidence"),
    Tutorial("03", "spatial-transcriptomic-analyses", "spatial_transcriptomic_analyses.ipynb", "Spatial transcriptomic analyses", "SVG · domains · deconvolution"),
    Tutorial("04", "clinical-prediction", "clinical_prediction.ipynb", "Clinical prediction", "WSI · prognosis"),
    Tutorial("05", "st-atlas-retrieval", "st_atlas_retrieval.ipynb", "ST atlas retrieval", "Text · H&E retrieval"),
)


def fragment(markup: str):
    return BeautifulSoup(markup, "html.parser")


def build_header() -> BeautifulSoup:
    return fragment(
        """
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span><span class="brand-copy"><strong>HistAgent</strong></span></a>
    <button class="nav-toggle" type="button" data-nav-toggle aria-expanded="false" aria-controls="primary-nav">Menu</button>
    <nav class="primary-nav" id="primary-nav" data-primary-nav aria-label="Primary navigation">
      <a href="/">Overview</a><a href="/tutorials/" aria-current="page">Tutorials</a><a href="/histagent/">HistAgent</a><a href="/atlas-explorer/">Atlas Explorer</a><a href="https://github.com/zipging/HistAgent">GitHub</a>
    </nav>
  </div>
</header>
"""
    )


def build_sidebar(current: Tutorial, headings: list[tuple[str, str]]) -> BeautifulSoup:
    groups = [
        '<div class="nb-nav-group"><a class="nb-nav-chapter" href="/tutorials/"><span class="nb-nav-number">00</span><span><strong>Overview</strong><small>Choose a tutorial</small></span></a></div>'
    ]
    for tutorial in TUTORIALS:
        current_attr = ' aria-current="page"' if tutorial == current else ""
        subnav = ""
        if tutorial == current:
            links = "".join(f'<a href="#{anchor}">{label}</a>' for anchor, label in headings)
            subnav = f'<nav class="nb-subnav" aria-label="{tutorial.title} sections">{links}</nav>'
        groups.append(
            f'<div class="nb-nav-group"><a class="nb-nav-chapter" href="/tutorials/{tutorial.slug}/"{current_attr}>'
            f'<span class="nb-nav-number">{tutorial.number}</span><span><strong>{tutorial.title}</strong><small>{tutorial.summary}</small></span></a>{subnav}</div>'
        )
    return fragment(
        '<aside class="nb-sidebar" aria-label="Tutorial directory">'
        '<div class="nb-sidebar-heading"><span class="nb-sidebar-kicker">HistAgent</span><h2>Tutorials</h2><p>Five executable tutorials with code and outputs.</p></div>'
        f'<nav class="nb-tutorial-nav">{"".join(groups)}</nav>'
        '<div class="nb-sidebar-note"><strong>Downloadable notebooks</strong><span>Use the link at the top of each tutorial page.</span></div>'
        '</aside>'
    )


def build_footer() -> BeautifulSoup:
    return fragment(
        """
<footer class="site-footer"><div class="page-shell footer-grid">
  <div><strong>HistAgent</strong><span class="fine-print">Spatial molecular analysis from routine histology.</span></div>
  <div class="footer-links"><a href="https://github.com/zipging/HistAgent">GitHub</a><a href="https://huggingface.co/wli13/HistAgent">Checkpoint</a><a href="https://huggingface.co/datasets/wli13/HistAgent-data">Data</a><a href="/tutorials/">Tutorials</a></div>
</div></footer>
"""
    )


def append_fragment(parent, html_fragment: BeautifulSoup) -> None:
    for child in list(html_fragment.contents):
        parent.append(child)


def render_tutorial(tutorial: Tutorial) -> None:
    source = NOTEBOOK_DIR / tutorial.notebook
    exporter = HTMLExporter(template_name="classic")
    exporter.exclude_input_prompt = True
    exporter.exclude_output_prompt = True
    rendered, _ = exporter.from_filename(str(source))
    soup = BeautifulSoup(rendered, "html.parser")

    soup.title.string = f"{tutorial.title} | HistAgent tutorials"
    head = soup.head
    for markup in (
        '<meta name="theme-color" content="#17362f">',
        '<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">',
        '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">',
        '<link rel="stylesheet" href="/assets/site.css?v=20260727paper2">',
        f'<link rel="stylesheet" href="/assets/notebook.css?v={ASSET_VERSION}">',
    ):
        append_fragment(head, fragment(markup))

    notebook_main = soup.body.find("main")
    if notebook_main is None:
        raise RuntimeError(f"No notebook main element found in {source}")
    notebook_main.extract()
    headings = [
        (heading.get("id"), heading.get_text(" ", strip=True).replace("¶", "").strip())
        for heading in notebook_main.select("h2[id]")
    ]

    body = soup.body
    body.clear()
    append_fragment(body, fragment('<a class="skip-link" href="#tutorial-main">Skip to content</a>'))
    append_fragment(body, build_header())

    workspace = soup.new_tag("div", attrs={"class": "nb-workspace"})
    append_fragment(workspace, build_sidebar(tutorial, headings))
    page = soup.new_tag("main", attrs={"class": "nb-page", "id": "tutorial-main"})
    toolbar = fragment(
        f'<div class="nb-toolbar"><span><a href="/tutorials/">Tutorials</a><span aria-hidden="true"> / </span><strong>{tutorial.title}</strong></span>'
        f'<a class="nb-download" href="/notebooks/{tutorial.notebook}" download>Download .ipynb</a></div>'
    )
    append_fragment(page, toolbar)
    page.append(notebook_main)
    workspace.append(page)
    body.append(workspace)
    append_fragment(body, build_footer())
    append_fragment(body, fragment('<script src="/assets/site.js?v=20260727paper2"></script>'))
    append_fragment(body, fragment(f'<script src="/assets/notebook.js?v={ASSET_VERSION}"></script>'))

    destination = TUTORIAL_DIR / tutorial.slug / "index.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    clean_html = "\n".join(line.rstrip() for line in str(soup).splitlines()) + "\n"
    destination.write_text(clean_html, encoding="utf-8")
    print(f"Rendered {destination.relative_to(ROOT)}")


def main() -> None:
    for tutorial in TUTORIALS:
        render_tutorial(tutorial)


if __name__ == "__main__":
    main()
