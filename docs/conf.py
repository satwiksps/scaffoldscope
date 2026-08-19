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
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
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
autodoc_typehints = "description"
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
    ("py:class", "_PluginHandle"),
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

extlinks = {
    "issue": ("https://github.com/satwiksps/scaffoldscope/issues/%s", "issue #%s"),
    "pull": ("https://github.com/satwiksps/scaffoldscope/pull/%s", "pull request #%s"),
}

html_theme = "furo"
html_title = f"ScaffoldScope {release} documentation"
html_logo = "assets/scaffoldscope.svg"
html_favicon = "assets/scaffoldscope.svg"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_show_sourcelink = True
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "https://scaffoldscope.readthedocs.io/")
html_theme_options = {
    "source_repository": "https://github.com/satwiksps/scaffoldscope/",
    "source_branch": "main",
    "source_directory": "docs/",
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/satwiksps/scaffoldscope",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0"
                     viewBox="0 0 16 16" aria-hidden="true" height="1em" width="1em"
                     xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 0C3.58 0 0 3.64 0 8.13c0 3.59 2.29 6.64 5.47 7.71.4.08.55-.18.55-.39
                  0-.19-.01-.83-.01-1.5-2.01.38-2.53-.5-2.69-.96-.09-.23-.48-.96-.82-1.15-.28-.15-.68-.53
                  -.01-.54.63-.01 1.08.59 1.23.83.72 1.23 1.87.88 2.33.67.07-.53.28-.88.51-1.08
                  -1.78-.21-3.64-.91-3.64-4.02 0-.89.31-1.62.82-2.19-.08-.2-.36-1.04.08-2.16 0 0 .67-.21
                  2.2.84A7.55 7.55 0 018 3.68c.68 0 1.36.09 2 .27 1.53-1.06 2.2-.84 2.2-.84.44 1.12.16
                  1.96.08 2.16.51.57.82 1.3.82 2.19 0 3.12-1.87 3.81-3.65 4.02.29.25.54.73.54 1.48
                  0 1.07-.01 1.93-.01 2.2 0 .21.15.47.55.39A8.02 8.02 0 0016 8.13C16 3.64 12.42 0 8 0z"/>
                </svg>
            """,
            "class": "",
        }
    ],
}

ogp_site_url = html_baseurl
ogp_site_name = "ScaffoldScope documentation"
ogp_image = "https://raw.githubusercontent.com/satwiksps/scaffoldscope/main/docs/assets/github-social-preview.png"
ogp_type = "website"

copybutton_prompt_text = r"^\$ |^> |^PS> "
copybutton_prompt_is_regexp = True
copybutton_only_copy_prompt_lines = True

linkcheck_anchors = True
linkcheck_timeout = 20
linkcheck_retries = 2
