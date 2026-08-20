---
name: web-screenshot
description: Capture a specified HTTP(S) webpage as a verified PNG with viewport, full-page, or unique-element modes.
---

# Web Screenshot

Use this skill when the user gives a concrete webpage URL and asks for a
screenshot. It is independent from the platform-specific `capture` command.

## Workflow

1. Require the user's exact HTTP(S) URL and an explicit output directory. Do
   not invent a URL or write into the repository.
2. Run one of these commands:

   ```powershell
   social-capture webpage "https://example.com" --output-dir "E:\temp\web"
   social-capture webpage "https://example.com" --output-dir "E:\temp\web" --mode full-page
   social-capture webpage "https://example.com" --output-dir "E:\temp\web" --mode element --selector "main article"
   ```

3. Use `--viewport WIDTHxHEIGHT` when a deterministic viewport is needed; the
   default is `1440x900`. `--wait SECONDS` controls the post-navigation wait.
4. Read `manifest.json` after the command. Treat the capture as successful only
   when the item has `status: "ok"` and contains at least one part, then verify
   the PNG exists and its recorded SHA-256, dimensions, mode, and final URL are
   usable.

## Modes and boundaries

- `viewport` captures the current viewport and is the default.
- `full-page` captures one PNG of the complete document. Before the screenshot
  it performs a finite lazy-load warm-up over the document height recorded
  immediately after navigation: it scrolls in bounded viewport-sized steps,
  waits briefly at each step, and waits a bounded time for current images and
  fonts. If lazy loading grows the document, it rescans only the newly added
  range until the height is stable or the round limit is reached, then returns
  to the top. It never auto-scrolls indefinitely. The manifest records
  `image_count`, `loaded`, `failed`, `pending`, `initial_height`,
  `final_warmup_height`, `rounds`, `executed_steps`, and `timed_out`;
  individual external image failures do not fail the page capture. The
  warm-up is capped at 4 rounds, 64 scroll positions total, 120 ms per
  step/poll, and 10 seconds total.
- `element` requires `--selector` and succeeds only when exactly one matching
  element is visible. Zero or multiple visible matches are failures.
- The command accepts HTTP(S) URLs only. It does not support batches,
  cookie-file input, PDF/JPEG output, device emulation, or login bypass.
- The command first tries the configured Chrome CDP endpoint. If it cannot
  connect, the webpage command may launch and clean up a temporary Chrome for
  public pages; platform capture retains its existing CDP requirements.

If navigation, element matching, or browser capture fails, report the manifest
failure and its error kind. Never expose Cookie values, tokens, signatures, or
unsanitized signed URLs in a response; use the redacted manifest URL instead.
