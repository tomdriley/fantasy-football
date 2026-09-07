"""Source, build-contract and native Node controller checks for the season UI."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


WEB = Path(__file__).resolve().parent.parent / "web" / "season"
HTML = (WEB / "index.html").read_text()
SOURCES = "\n".join(path.read_text() for path in sorted((WEB / "src").glob("*.*")))


class Markup(HTMLParser):
    def __init__(self, source=HTML):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class TestSeasonAssets(unittest.TestCase):
    def test_framework_and_reproducible_build_contract(self):
        package = json.loads((WEB / "package.json").read_text())
        lock = json.loads((WEB / "package-lock.json").read_text())
        self.assertTrue(package["private"])
        for dependency in ("react", "react-dom", "@mui/material", "@emotion/cache"):
            self.assertIn(dependency, package["dependencies"])
        for tool in ("typescript", "vite"):
            self.assertIn(tool, package["devDependencies"])
        self.assertEqual(lock["packages"][""]["dependencies"], package["dependencies"])
        self.assertIn("tsc", package["scripts"]["typecheck"])
        self.assertIn("vite build", package["scripts"]["build"])
        self.assertIn("node --test", package["scripts"]["test"])
        config = (WEB / "vite.config.ts").read_text()
        self.assertIn("outDir: 'dist'", config)
        self.assertIn("emptyOutDir: false", config)
        self.assertIn("assetsDir: 'assets'", config)
        self.assertIn("manifest: true", config)
        self.assertFalse((WEB / "app.js").exists())
        self.assertFalse((WEB / "style.css").exists())
        ignores = (WEB / ".gitignore").read_text()
        self.assertIn("node_modules/", ignores)
        self.assertIn("dist/", ignores)

    def test_nonce_and_safe_local_entrypoint(self):
        self.assertIn('name="csp-nonce" content="__FFOPT_CSP_NONCE__"', HTML)
        self.assertIn('<script type="module" src="/src/main.tsx"></script>', HTML)
        self.assertIn("CacheProvider", SOURCES)
        self.assertIn("createCache({ key: 'ffopt', nonce })", SOURCES)
        self.assertIn("meta[name=\"csp-nonce\"]", SOURCES)
        self.assertNotIn("unsafe-inline", HTML)
        for tag, attrs in Markup().elements:
            self.assertNotEqual(tag, "style")
            self.assertNotIn("style", attrs)
            self.assertFalse(any(key.startswith("on") for key in attrs))
            if tag in ("script", "link", "img"):
                self.assertTrue(attrs.get("src", attrs.get("href", "")).startswith("/"))
        for sink in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(sink, SOURCES)
        self.assertNotIn("fonts.googleapis", SOURCES)
        self.assertNotIn("@mui/x-data-grid-pro", SOURCES)

    def test_token_and_navigation_safety(self):
        self.assertIn("HashRouter", SOURCES)
        self.assertIn("type=\"password\"", SOURCES)
        self.assertIn("setToken('')", SOURCES)
        self.assertIn("#token", SOURCES)
        self.assertNotIn("localStorage", SOURCES)
        self.assertNotIn("document.cookie", SOURCES)
        self.assertIn("url.origin !== this.origin", SOURCES)
        self.assertIn("redirect: 'error'", SOURCES)
        self.assertIn("AbortController", SOURCES)
        self.assertIn("crypto.randomUUID()", SOURCES)
        self.assertIn("headers['Idempotency-Key']", SOURCES)
        for path in ("/api/v1/advice", "/api/v1/collections", "/api/v1/jobs?limit=20"):
            self.assertIn(path, SOURCES)
        self.assertIn("same request", SOURCES)

    def test_product_and_accessibility_guards(self):
        week = (WEB / "src" / "Week.tsx").read_text()
        self.assertIn("Update advice", week)
        self.assertIn("What needs attention", week)
        self.assertIn("Next lineup deadline", week)
        self.assertIn("Questionable means uncertain, not ruled out", week)
        self.assertIn("around 90 minutes before", week)
        self.assertIn("No automatic reminder is sent yet", week)
        self.assertNotIn("setSnapshotId", week)
        self.assertNotIn("setEvaluationId", week)
        self.assertIn("Previous lineup · reference only", SOURCES)
        self.assertIn("Historical report — do not treat as current advice", SOURCES)
        self.assertIn("Skip to content", SOURCES)
        self.assertIn('aria-live="polite"', SOURCES)
        self.assertIn("prefers-reduced-motion", SOURCES)
        self.assertIn(":focus-visible", SOURCES)
        self.assertIn("system-ui", SOURCES)
        self.assertIn("Nothing is applied automatically", SOURCES)
        self.assertNotRegex(SOURCES, r"<AccordionDetails\s+id=", "MUI assigns the controlled region ID itself")

    def test_lazy_route_failure_keeps_navigation_and_reload_available(self):
        app = (WEB / "src" / "App.tsx").read_text()
        boundary = (WEB / "src" / "RouteErrorBoundary.tsx").read_text()
        self.assertIn("<RouteErrorBoundary key={location.pathname}><Suspense", app)
        self.assertLess(app.index("</AppBar>"), app.index("<RouteErrorBoundary"))
        self.assertIn("getDerivedStateFromError", boundary)
        self.assertIn("window.location.reload()", boundary)
        self.assertIn("Reload page", boundary)
        self.assertIn('to="/"', boundary)
        self.assertIn("Go to My week", boundary)

    @unittest.skipUnless((WEB / "dist" / "index.html").exists(), "Run npm run build to check emitted assets")
    def test_built_assets_are_hashed_local_and_nonce_ready(self):
        built = (WEB / "dist" / "index.html").read_text()
        self.assertIn("__FFOPT_CSP_NONCE__", built)
        manifest = json.loads((WEB / "dist" / ".vite" / "manifest.json").read_text())
        self.assertIn("index.html", manifest)
        for tag, attrs in Markup(built).elements:
            if tag in ("script", "link"):
                path = attrs.get("src", attrs.get("href", ""))
                self.assertRegex(path, r"^/assets/.+-[A-Za-z0-9_-]+\.(js|css)$")
                self.assertTrue((WEB / "dist" / path.lstrip("/")).is_file())
        self.assertNotIn("/src/", built)
        self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", built))


@unittest.skipUnless(shutil.which("node"), "Node 24 is required for TypeScript controller tests")
class TestSeasonControllers(unittest.TestCase):
    def test_native_node_controller_tests(self):
        version = subprocess.run([shutil.which("node"), "--version"], text=True, capture_output=True, check=True)
        if int(version.stdout.lstrip("v").split(".")[0]) < 24:
            self.skipTest("The frontend requires Node 24 or newer")
        tests = sorted(str(path.relative_to(WEB)) for path in (WEB / "tests").glob("*.test.ts"))
        self.assertGreaterEqual(len(tests), 3)
        result = subprocess.run(
            [shutil.which("node"), "--test", *tests],
            cwd=WEB, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
