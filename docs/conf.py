from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scaffoldscope import __version__  # noqa: E402

project = "ScaffoldScope"
author = "Satwik Sai Prakash Sahoo and contributors"
copyright = "2026, Satwik Sai Prakash Sahoo and contributors"
version = __version__
release = __version__

extensions = [
    "myst_parser",
    "sphinx_immaterial",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_design",
    "sphinxcontrib.programoutput",
    "sphinxext.opengraph",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = [
    "attrs_block",
    "colon_fence",
    "deflist",
    "fieldlist",
    "linkify",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3
myst_url_schemes = ("http", "https", "mailto")

autosectionlabel_prefix_document = True
autodoc_member_order = "bysource"
autodoc_typehints = "signature"
autodoc_typehints_format = "short"
autodoc_type_aliases = {
    "Char4TokenCounter": "scaffoldscope.tokenization.Char4TokenCounter",
    "ModelConfig": "scaffoldscope.schema.ModelConfig",
    "TaskSpec": "scaffoldscope.schema.TaskSpec",
    "VariantConfig": "scaffoldscope.schema.VariantConfig",
}
napoleon_google_docstring = True
napoleon_numpy_docstring = False
nitpicky = True
nitpick_ignore = [
    ("py:class", "scaffoldscope.plugins.FactoryT"),
    ("py:class", "scaffoldscope.plugins._EntryPoint"),
    ("py:class", "scaffoldscope.plugins._PluginHandle"),
    ("py:class", "_PluginHandle"),
    ("py:param", "FactoryT"),
]
nitpick_ignore_regex = [
    (r"py:class", r"pathlib\..*\.Path"),
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

extlinks = {
    "issue": ("https://github.com/satwiksps/scaffoldscope/issues/%s", "issue #%s"),
    "pull": ("https://github.com/satwiksps/scaffoldscope/pull/%s", "pull request #%s"),
}

site_url = (
    os.environ.get("READTHEDOCS_CANONICAL_URL", "https://scaffoldscope.readthedocs.io/").rstrip("/")
    + "/"
)

html_theme = "sphinx_immaterial"
html_title = "ScaffoldScope"
html_logo = "assets/scaffoldscope-mark.svg"
html_favicon = "assets/scaffoldscope-mark.svg"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_show_sourcelink = True
html_baseurl = site_url
html_theme_options = {
    "site_url": site_url,
    "repo_url": "https://github.com/satwiksps/scaffoldscope",
    "repo_name": "satwiksps/scaffoldscope",
    "edit_uri": "edit/main/docs/",
    "icon": {
        "repo": "fontawesome/brands/github",
        "edit": "material/file-edit-outline",
    },
    "features": [
        "content.action.edit",
        "content.code.annotate",
        "content.code.copy",
        "content.tabs.link",
        "navigation.sections",
        "navigation.expand",
        "navigation.indexes",
        "navigation.instant",
        "navigation.instant.progress",
        "navigation.top",
        "navigation.footer",
        "search.highlight",
        "search.share",
        "search.suggest",
        "toc.follow",
        "toc.sticky",
    ],
    "palette": [
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "primary": "custom",
            "accent": "custom",
            "toggle": {
                "icon": "material/weather-sunny",
                "name": "Use light mode",
            },
        },
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "primary": "custom",
            "accent": "custom",
            "toggle": {
                "icon": "material/weather-night",
                "name": "Use dark mode",
            },
        },
    ],
    "font": False,
    "globaltoc_collapse": False,
    "toc_title": "On this page",
}

ogp_site_url = site_url
ogp_site_name = "ScaffoldScope documentation"
ogp_image = "https://raw.githubusercontent.com/satwiksps/scaffoldscope/main/docs/assets/github-social-preview.png"
ogp_type = "website"

linkcheck_anchors = True
linkcheck_timeout = 20
linkcheck_retries = 2


def _normalize_edit_url(
    app: object,
    pagename: str,
    templatename: str,
    context: dict[str, object],
    doctree: object,
) -> None:
    page = context.get("page")
    if isinstance(page, dict) and isinstance(page.get("edit_url"), str):
        page["edit_url"] = page["edit_url"].replace("\\", "/")


def setup(app: object) -> None:
    app.connect("html-page-context", _normalize_edit_url, priority=800)
