# Provider API

Providers are intentionally static in v0.1. A provider owns URL parsing,
platform DOM selectors, and platform-specific browser preparation. The core
owns authentication loading, output paths, image hashing, manifests, and long
image splitting.

Implement `CaptureProvider` in `src/social_capture/providers/<name>/provider.py`:

```python
class ExampleProvider(CaptureProvider):
    name = "example"
    hosts = ("example.com",)
    capabilities = ("post-card",)

    def can_handle(self, value: str) -> bool: ...
    def parse_reference(self, value: str) -> CaptureReference: ...
    async def capture(self, browser, reference, options) -> CaptureArtifact: ...
```

Return `CapturedImage` objects in a `CaptureArtifact`. Set `split_long=True`
only when the platform card is a continuous long document. If the platform
has a carousel, return one image per frame and use labels in metadata. Never
fall back to a full-page screenshot: a provider must fail with a target
localization error when it cannot identify the requested card.

Add the provider instance to `BUILTIN_PROVIDERS` in `registry.py`, add URL and
failure tests, and document any platform-specific authentication environment
variable. Search is not a Provider capability. Add an entry to `search.py`
instead when an external search project should be suggested.

## Browser rules

Use `open_site_page`, `check_page_state`, and `capture_locator` helpers. Attach
to the configured Chrome CDP endpoint; do not close existing user pages. Use a
temporary Chrome only through `BrowserSession`, which requires explicit Cookie
input when the endpoint is unavailable.

## Output rules

The CLI requires `--output-dir`. Do not introduce a default output folder.
Provider metadata must not contain cookies, signed query values, or full page
HTML. `manifest.json` is the public evidence record and is redacted once more
by `OutputWriter`.
