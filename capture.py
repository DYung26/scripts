#!/usr/bin/env python3
"""Scroll through a tab, expand collapsed content as it mounts, and stitch
per-viewport screenshots into a single full-page image (or, with --pdf,
per-scroll-step PDF pages into a single multi-page PDF; or, with --text,
the page's extracted text instead of any image/PDF output).

Two ways to get a page to capture:

Attach to an already-running browser over CDP (requires it to have been
launched with a remote debugging port), e.g.:
    msedge --remote-debugging-port=9222 --user-data-dir=<existing profile path>
    python capture.py --url-match claude.ai --output-dir ./out

Launch a fresh browser and navigate it yourself:
    python capture.py --launch --url https://claude.ai --output-dir ./out

For --pdf, launch mode with --headless is the reliable path (see the
print_page_to_pdf docstring for why attach/headful mode is a gamble):
    python capture.py --launch --headless --url https://claude.ai --pdf --output-dir ./out
"""

import argparse
import asyncio
import base64
import io
from collections.abc import Awaitable, Callable
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from pypdf import PdfReader, PdfWriter

EXPAND_TOGGLES_JS = """
() => {
  const toggles = document.querySelectorAll('button.group\\\\/status[aria-expanded="false"]');
  toggles.forEach((btn) => btn.click());
  return toggles.length;
}
"""

CLICK_SHOW_MORE_JS = """
() => {
  const buttons = Array.from(document.querySelectorAll('button'))
    .filter((btn) => btn.textContent.trim() === 'Show more');
  buttons.forEach((btn) => btn.click());
  return buttons.length;
}
"""

FIND_SCROLL_CONTAINER_JS = """
() => {
  if (document.querySelector('[data-fpsc-scroll-target="true"]')) return true;
  const candidates = Array.from(document.querySelectorAll('*'));
  let best = null;
  let bestScore = 0;
  for (const el of candidates) {
    const style = getComputedStyle(el);
    if (!/(auto|scroll)/.test(style.overflowY)) continue;
    const overflowAmount = el.scrollHeight - el.clientHeight;
    if (overflowAmount > bestScore && el.clientHeight > window.innerHeight * 0.5) {
      best = el;
      bestScore = overflowAmount;
    }
  }
  if (best) {
    best.dataset.fpscScrollTarget = 'true';
    return true;
  }
  return false;
}
"""

SCROLL_INFO_JS = """
() => {
  const el = document.querySelector('[data-fpsc-scroll-target="true"]');
  if (el) {
    return { scrollY: el.scrollTop, viewportHeight: el.clientHeight, scrollHeight: el.scrollHeight };
  }
  return {
    scrollY: window.scrollY,
    viewportHeight: window.innerHeight,
    scrollHeight: document.documentElement.scrollHeight,
  };
}
"""

SCROLL_TO_JS = """
(y) => {
  const el = document.querySelector('[data-fpsc-scroll-target="true"]');
  if (el) {
    el.scrollTop = y;
  } else {
    window.scrollTo(0, y);
  }
}
"""

HIDE_BOTTOM_OVERLAYS_JS = """
() => {
  const candidates = Array.from(document.querySelectorAll('*'));
  let hidden = 0;
  for (const el of candidates) {
    const style = getComputedStyle(el);
    if (style.position !== 'fixed' && style.position !== 'sticky') continue;
    const rect = el.getBoundingClientRect();
    if (rect.height === 0 || rect.width === 0) continue;
    const hugsBottom = rect.bottom >= window.innerHeight - 200;
    const wideEnough = rect.width >= 300;
    const shortEnough = rect.height <= window.innerHeight * 0.5;
    if (hugsBottom && wideEnough && shortEnough) {
      el.dataset.fpscHiddenBottom = 'true';
      el.style.setProperty('display', 'none', 'important');
      hidden += 1;
    }
  }
  return hidden;
}
"""

SHOW_BOTTOM_OVERLAYS_JS = """
() => {
  const els = document.querySelectorAll('[data-fpsc-hidden-bottom="true"]');
  els.forEach((el) => {
    el.style.removeProperty('display');
    delete el.dataset.fpscHiddenBottom;
  });
  return els.length;
}
"""

HIDE_TOP_OVERLAYS_JS = """
() => {
  const candidates = Array.from(document.querySelectorAll('*'));
  let hidden = 0;
  for (const el of candidates) {
    const style = getComputedStyle(el);
    if (style.position !== 'fixed' && style.position !== 'sticky') continue;
    const rect = el.getBoundingClientRect();
    if (rect.height === 0 || rect.width === 0) continue;
    const hugsTop = rect.top <= 200;
    const wideEnough = rect.width >= 300;
    const shortEnough = rect.height <= window.innerHeight * 0.5;
    if (hugsTop && wideEnough && shortEnough) {
      el.dataset.fpscHiddenTop = 'true';
      el.style.setProperty('display', 'none', 'important');
      hidden += 1;
    }
  }
  return hidden;
}
"""

SHOW_TOP_OVERLAYS_JS = """
() => {
  const els = document.querySelectorAll('[data-fpsc-hidden-top="true"]');
  els.forEach((el) => {
    el.style.removeProperty('display');
    delete el.dataset.fpscHiddenTop;
  });
  return els.length;
}
"""

EXTRACT_VIEWPORT_TEXT_JS = """
() => {
  const root = document.querySelector('[data-fpsc-scroll-target="true"]') || document.body;
  const rootRect = root.getBoundingClientRect();
  const top = root === document.body ? 0 : rootRect.top;
  const bottom = root === document.body ? window.innerHeight : rootRect.bottom;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      const style = getComputedStyle(parent);
      if (style.display === 'none' || style.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      if (rect.bottom < top || rect.top > bottom) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const lines = [];
  let currentParent = null;
  let currentLine = '';
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (parent !== currentParent) {
      if (currentLine) lines.push(currentLine);
      currentLine = '';
      currentParent = parent;
    }
    currentLine += node.nodeValue;
  }
  if (currentLine) lines.push(currentLine);
  return lines.join('\\n');
}
"""

SWITCHBOARD_FIND_ACCOUNT_JS = """
({ extensionId, accountName }) => {
  return new Promise((resolve, reject) => {
    const origin = window.location.origin;
    chrome.runtime.sendMessage(
      extensionId,
      { type: 'switchboard/account/list', payload: { origin } },
      (response) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (response && response.error) return reject(new Error(response.error));
        const match = (response || []).find((a) => a.name === accountName);
        if (!match) return reject(new Error(`No Switchboard account named "${accountName}" for ${origin}`));
        resolve({ accountId: match.id, origin });
      },
    );
  });
}
"""

SWITCHBOARD_TRIGGER_SWITCH_JS = """
({ extensionId, accountId, origin }) => {
  chrome.runtime.sendMessage(extensionId, {
    type: 'switchboard/account/switch',
    payload: { accountId, origin },
  });
}
"""


async def wait_for_height_settle(page: Page, checks: int, interval_ms: int) -> dict:
    stable = 0
    last_height = None
    info = await page.evaluate(SCROLL_INFO_JS)
    while stable < checks:
        await page.wait_for_timeout(interval_ms)
        info = await page.evaluate(SCROLL_INFO_JS)
        if info["scrollHeight"] == last_height:
            stable += 1
        else:
            stable = 0
            last_height = info["scrollHeight"]
    return info


async def wait_for_page_ready(page: Page, target_url: str, settle_checks: int, settle_interval_ms: int) -> bool:
    """Wait for the page to settle, returning whether it navigated away from `target_url`.

    `target_url` must be the URL that was actually requested (e.g. the `--url`
    argument), not `page.url` sampled after navigation already happened. A
    fast redirect (e.g. straight to a login page) can already be resolved by
    the time `page.goto()` returns, in which case reading `page.url` at that
    point would silently capture the post-redirect URL as the baseline and
    this function would never see a departure worth reacting to.

    A session that can't load `target_url` as-is bounces itself elsewhere
    sometime within this same settle window - either to claude.ai/login when
    unauthenticated, or to the home page at claude.ai/new when the signed-in
    account doesn't own the conversation being loaded. A still-valid session
    for the right account never changes URL. Watching for that departure
    here doubles as the signal for whether Switchboard's account switch is
    actually needed - a page that stays on `target_url` doesn't need
    re-authenticating.
    """
    navigated_away = False

    def _on_frame_navigated(frame) -> None:
        nonlocal navigated_away
        if frame.parent_frame is None and frame.url != target_url:
            navigated_away = True

    page.on("framenavigated", _on_frame_navigated)
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeoutError:
            pass
        await wait_for_height_settle(page, settle_checks, settle_interval_ms)
    finally:
        page.remove_listener("framenavigated", _on_frame_navigated)

    return navigated_away or page.url != target_url


async def expand_current_viewport(page: Page, settle_ms: int) -> None:
    await page.evaluate(EXPAND_TOGGLES_JS)
    await page.wait_for_timeout(settle_ms)
    await page.evaluate(CLICK_SHOW_MORE_JS)
    await page.wait_for_timeout(settle_ms)


MAX_MEAN_CHANNEL_DIFF = 4.0


def _rows_match(img: Image.Image, top_of: Image.Image, prev_start: int, next_start: int, height: int) -> bool:
    """Compare a horizontal band of `height` rows between two crops for near-equality.

    Two captures of the same scrolled content can still differ by a few pixel values
    (anti-aliasing, a blinking cursor, hover state) even when nothing has actually
    moved, so an exact byte comparison misses real overlaps. Tolerate small
    per-channel differences instead of requiring an identical match.
    """
    prev_band = img.crop((0, prev_start, img.width, prev_start + height)).convert("RGB")
    next_band = top_of.crop((0, next_start, top_of.width, next_start + height)).convert("RGB")
    diff = ImageChops.difference(prev_band, next_band)
    return max(ImageStat.Stat(diff).mean) <= MAX_MEAN_CHANNEL_DIFF


def _find_overlap(prev_img: Image.Image, next_img: Image.Image, expected_overlap: int, search_radius: int = 40, probe_height: int = 20) -> int:
    """Find how many rows at the bottom of prev_img duplicate rows at the top of next_img.

    The capture loop deliberately re-scrolls by `expected_overlap` fewer pixels than a
    full viewport, so consecutive shots are already known to share close to that many
    duplicate rows; layout reflow between shots can only shift the true amount by a
    little. Searching outward from that expected value and accepting the closest
    confirmed match (rather than scanning for the largest match anywhere up to an
    arbitrary cap) avoids false matches inside blank/whitespace runs, which can look
    identical at many unrelated offsets and would otherwise crop real content.
    """
    max_overlap = min(prev_img.height, next_img.height)
    low = max(1, expected_overlap - search_radius)
    high = min(max_overlap, expected_overlap + search_radius)
    for overlap in sorted(range(low, high + 1), key=lambda o: abs(o - expected_overlap)):
        band_height = min(probe_height, overlap)
        if _rows_match(prev_img, next_img, prev_img.height - overlap, 0, band_height):
            return overlap
    return min(expected_overlap, max_overlap)


def stitch(shots: list[tuple[int, bytes]], out_dir: Path, expected_overlap: int, max_height: int = 8000) -> list[Path]:
    images = [Image.open(io.BytesIO(png)) for _, png in shots]
    width = images[0].width

    # Sequentially stack shots, detecting and trimming any duplicated rows between
    # each consecutive pair rather than trusting the recorded scroll position for
    # placement (layout shifts between shots can make that position slightly off).
    trimmed = [images[0]]
    for prev_img, next_img in zip(images, images[1:]):
        overlap = _find_overlap(prev_img, next_img, expected_overlap)
        trimmed.append(next_img.crop((0, overlap, width, next_img.height)) if overlap else next_img)

    # Roll over into a new output file whenever the next image would push the
    # current one past max_height, instead of building one unbounded image.
    chunks: list[list[Image.Image]] = []
    current: list[Image.Image] = []
    current_height = 0
    for img in trimmed:
        if current and current_height + img.height > max_height:
            chunks.append(current)
            current = []
            current_height = 0
        current.append(img)
        current_height += img.height
    if current:
        chunks.append(current)

    out_paths: list[Path] = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_height = sum(img.height for img in chunk)
        canvas = Image.new("RGB", (width, chunk_height), "white")
        y = 0
        for img in chunk:
            canvas.paste(img, (0, y))
            y += img.height
        name = "stitched.png" if len(chunks) == 1 else f"stitched_{idx:03d}.png"
        out_path = out_dir / name
        canvas.save(out_path)
        out_paths.append(out_path)

    return out_paths


def merge_text_snapshots(snapshots: list[str], max_overlap_lines: int = 40) -> str:
    """Concatenate per-scroll-step viewport text, trimming the duplicate lines each
    consecutive pair shares from deliberately overlapping scroll positions.

    Mirrors stitch()'s approach for images: search backward from the end of the
    previous snapshot's lines for the longest run that exactly matches the start
    of the next snapshot's lines, and drop that many duplicate lines from the
    next snapshot before appending it. Unlike stitch(), there's no expected
    overlap to search near - scroll distance in pixels doesn't map to a known
    number of text lines - so this scans every candidate overlap length down
    from the cap instead of searching outward from an estimate.
    """
    if not snapshots:
        return ""

    merged_lines = snapshots[0].split("\n")
    for snapshot in snapshots[1:]:
        next_lines = snapshot.split("\n")
        cap = min(max_overlap_lines, len(merged_lines), len(next_lines))
        overlap = 0
        for candidate in range(cap, 0, -1):
            if merged_lines[-candidate:] == next_lines[:candidate]:
                overlap = candidate
                break
        merged_lines.extend(next_lines[overlap:])

    return "\n".join(merged_lines)


async def print_page_to_pdf(page: Page) -> bytes:
    """Print the page's current DOM state to a PDF, headless-first with a CDP fallback.

    Chrome's print-to-PDF pipeline (Page.printToPDF) is dependable in headless
    mode. In a headful window - which is how both launch_page and attach_page
    run by default - it can raise or (on older Chrome) silently fail. Try
    Playwright's high-level page.pdf() first since it's simpler and does work
    headful on some Chrome versions; if it raises, fall back to calling the
    same CDP command directly, which occasionally succeeds where the wrapper
    refuses. If both fail, the caller is in headful attach mode and should
    switch to `--launch --headless` instead - there's no reliable in-process
    workaround for a browser window that's already running headful.
    """
    try:
        return await page.pdf(print_background=True, prefer_css_page_size=True)
    except Exception:
        pass

    client = await page.context.new_cdp_session(page)
    result = await client.send(
        "Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True}
    )
    return base64.b64decode(result["data"])


def merge_pdfs(pdf_paths: list[Path], out_dir: Path, max_pages: int) -> list[Path]:
    """Concatenate per-scroll-step PDFs into one or more multi-page documents.

    Unlike the image path, this doesn't try to detect and trim duplicate
    content between consecutive steps: a few repeated lines of text across a
    page boundary cost a handful of extra tokens, nowhere near what a
    duplicated band of pixels costs in an image, so the added complexity of
    matching text across steps isn't worth it here. Rolls over into a new
    output file whenever the next step's pages would push the current one
    past max_pages, mirroring stitch()'s height rollover, mainly to stay
    comfortably under per-request PDF page limits.
    """
    out_paths: list[Path] = []
    writer = PdfWriter()
    page_count = 0
    part = 1

    def flush() -> None:
        nonlocal writer, page_count, part
        if page_count == 0:
            return
        name = "merged.pdf" if part == 1 and len(pdf_paths) == 1 else f"merged_{part:03d}.pdf"
        out_path = out_dir / name
        with out_path.open("wb") as f:
            writer.write(f)
        out_paths.append(out_path)
        writer = PdfWriter()
        page_count = 0
        part += 1

    for pdf_path in pdf_paths:
        reader = PdfReader(pdf_path)
        for pdf_page in reader.pages:
            if page_count >= max_pages:
                flush()
            writer.add_page(pdf_page)
            page_count += 1
    flush()

    # Only one output file after all - rename it without the _NNN suffix for consistency.
    if len(out_paths) == 1 and out_paths[0].name != "merged.pdf":
        final_path = out_dir / "merged.pdf"
        out_paths[0].rename(final_path)
        out_paths = [final_path]

    return out_paths


async def attach_page(pw, cdp_url: str, url_match: str) -> tuple[Page, Callable[[], Awaitable[None]]]:
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = browser.contexts[0]
    page = next((p for p in context.pages if url_match in p.url), context.pages[0])
    await page.bring_to_front()
    return page, browser.close


async def launch_page(
    pw,
    executable_path: Path,
    user_data_dir: Path,
    load_extension: Path | None,
    url: str,
    headless: bool = False,
) -> tuple[Page, Callable[[], Awaitable[None]]]:
    args = ["--disable-blink-features=AutomationControlled", "--start-maximized"]
    if load_extension:
        args += [
            f"--disable-extensions-except={load_extension}",
            f"--load-extension={load_extension}",
        ]
    if headless and load_extension:
        # Unpacked extensions don't load in classic headless Chrome; --load-extension
        # is silently ignored there, so Switchboard account switching won't work
        # in this mode. Not fatal for --pdf if you're already on the right account.
        print("Warning: --headless with --load-extension set; the extension will not load headless.")
    context = await pw.chromium.launch_persistent_context(
        str(user_data_dir),
        executable_path=str(executable_path),
        headless=headless,
        args=args,
        no_viewport=not headless,
        color_scheme="dark",
        ignore_default_args=["--enable-automation"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url)
    return page, context.close


async def find_switchboard_extension_id(context) -> str:
    for worker in context.service_workers:
        if worker.url.startswith("chrome-extension://"):
            return worker.url.split("/")[2]
    worker = await context.wait_for_event("serviceworker", timeout=10_000)
    return worker.url.split("/")[2]


async def switch_account(page: Page, account_name: str) -> None:
    extension_id = await find_switchboard_extension_id(page.context)
    match = await page.evaluate(
        SWITCHBOARD_FIND_ACCOUNT_JS, {"extensionId": extension_id, "accountName": account_name}
    )
    async with page.expect_navigation(wait_until="load"):
        await page.evaluate(
            SWITCHBOARD_TRIGGER_SWITCH_JS,
            {"extensionId": extension_id, "accountId": match["accountId"], "origin": match["origin"]},
        )


async def capture(
    page: Page,
    output_dir: Path,
    settle_ms: int,
    settle_checks: int,
    settle_interval_ms: int,
    hide_bottom_overlays: bool = True,
    hide_top_overlays: bool = True,
    max_stitch_height: int = 16000,
    scroll_overlap_px: int = 150,
    pdf: bool = False,
    max_pdf_pages: int = 90,
    expand_only: bool = False,
    skip_expand: bool = False,
    text: bool = False,
    start_position: bool = False,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Neither expand_only nor text mode needs the fuller settle a screenshot
    # needs to render cleanly - they only need to know the DOM stopped growing
    # before moving on - so both can scroll through much faster. This matters
    # for text mode especially: reading whatever's on screen as soon as each
    # viewport is reachable beats reading it late, since already-expanded
    # content elsewhere on the page can idle back collapsed while a slower
    # pass is still working its way down.
    use_fast_settle = expand_only or text
    loop_settle_ms = 0 if use_fast_settle else settle_ms
    loop_settle_checks = 1 if use_fast_settle else settle_checks
    loop_settle_interval_ms = 50 if use_fast_settle else settle_interval_ms

    device_pixel_ratio = await page.evaluate("() => window.devicePixelRatio")
    expected_overlap_px = round(scroll_overlap_px * device_pixel_ratio)

    await wait_for_height_settle(page, settle_checks, settle_interval_ms)
    await page.evaluate(FIND_SCROLL_CONTAINER_JS)
    if start_position:
        await asyncio.to_thread(
            input, "Scroll/position the page where capture should start, then press Enter..."
        )
    else:
        await page.evaluate(SCROLL_TO_JS, 0)
    await wait_for_height_settle(page, settle_checks, settle_interval_ms)

    shots: list[tuple[int, bytes]] = []
    pdf_step_paths: list[Path] = []
    text_snapshots: list[str] = []
    last_scroll_y = -1
    is_first = True
    warned_no_bottom_overlay = False
    warned_no_top_overlay = False
    step = 0

    while True:
        if not skip_expand:
            await expand_current_viewport(page, loop_settle_ms)
        info = await wait_for_height_settle(page, loop_settle_checks, loop_settle_interval_ms)

        at_bottom = info["scrollY"] + info["viewportHeight"] >= info["scrollHeight"] - 1

        if text:
            snapshot = await page.evaluate(EXTRACT_VIEWPORT_TEXT_JS)
            if snapshot.strip():
                text_snapshots.append(snapshot)

        if not expand_only and not text:
            # The bottom overlay (e.g. a chat composer) only belongs in the shot that
            # actually shows the bottom of the page; hide it everywhere else.
            if hide_bottom_overlays:
                if at_bottom:
                    await page.evaluate(SHOW_BOTTOM_OVERLAYS_JS)
                else:
                    hidden_count = await page.evaluate(HIDE_BOTTOM_OVERLAYS_JS)
                    if hidden_count == 0 and not warned_no_bottom_overlay:
                        print("Warning: --keep-bottom-overlays is off but no matching overlay was found to hide.")
                        warned_no_bottom_overlay = True

            # Mirror that for the top overlay (e.g. a header), which only belongs in
            # the shot that actually shows the top of the page.
            if hide_top_overlays:
                if is_first:
                    await page.evaluate(SHOW_TOP_OVERLAYS_JS)
                else:
                    hidden_count = await page.evaluate(HIDE_TOP_OVERLAYS_JS)
                    if hidden_count == 0 and not warned_no_top_overlay:
                        print("Warning: --keep-top-overlays is off but no matching overlay was found to hide.")
                        warned_no_top_overlay = True

            if pdf:
                pdf_bytes = await print_page_to_pdf(page)
                step_path = output_dir / f"page_{step:03d}.pdf"
                step_path.write_bytes(pdf_bytes)
                pdf_step_paths.append(step_path)
            else:
                png = await page.screenshot()
                shots.append((info["scrollY"], png))

        if at_bottom or info["scrollY"] == last_scroll_y:
            break
        last_scroll_y = info["scrollY"]
        is_first = False
        step += 1

        next_y = info["scrollY"] + info["viewportHeight"] - scroll_overlap_px
        await page.evaluate(SCROLL_TO_JS, next_y)
        await page.wait_for_timeout(loop_settle_ms)

    if expand_only:
        return []

    if text:
        out_path = output_dir / "page.txt"
        out_path.write_text(merge_text_snapshots(text_snapshots))
        return [out_path]

    if pdf:
        return merge_pdfs(pdf_step_paths, output_dir, max_pdf_pages)

    for i, (scroll_y, png) in enumerate(shots):
        (output_dir / f"shot_{i:03d}_y{scroll_y}.png").write_bytes(png)

    return stitch(shots, output_dir, expected_overlap_px, max_height=max_stitch_height)


async def run(args: argparse.Namespace) -> list[Path]:
    async with async_playwright() as pw:
        if args.launch:
            page, cleanup = await launch_page(
                pw, args.executable_path, args.user_data_dir, args.load_extension, args.url,
                headless=args.headless,
            )
            # The URL actually requested, not wherever page.url ends up after a
            # redirect that may have already resolved by the time goto() returns.
            target_url = args.url
        else:
            if args.pdf:
                print(
                    "Warning: --pdf in attach mode is printing from an already-running headful "
                    "browser. This often fails - if it does, rerun with --launch --headless instead."
                )
            page, cleanup = await attach_page(pw, args.cdp_url, args.url_match)
            # Attach mode never requests a URL of its own - we're just grabbing
            # whatever tab matched `--url-match` - so there's no pre-redirect
            # baseline to recover here; page.url is the best available target.
            target_url = page.url

        needs_switch = await wait_for_page_ready(page, target_url, args.settle_checks, args.settle_interval_ms)

        if args.switchboard_account and needs_switch:
            await switch_account(page, args.switchboard_account)
            await page.goto(target_url)
            await wait_for_page_ready(page, target_url, args.settle_checks, args.settle_interval_ms)

        result = await capture(
            page,
            args.output_dir,
            args.settle_ms,
            args.settle_checks,
            args.settle_interval_ms,
            hide_bottom_overlays=not args.keep_bottom_overlays,
            hide_top_overlays=not args.keep_top_overlays,
            max_stitch_height=args.max_stitch_height,
            scroll_overlap_px=args.scroll_overlap,
            pdf=args.pdf,
            max_pdf_pages=args.max_pdf_pages,
            expand_only=args.expand_only,
            skip_expand=args.skip_expand,
            text=args.text,
            start_position=args.start_position,
        )

        if not args.no_prompt:
            await asyncio.to_thread(input, "Capture complete. Press Enter to close the browser...")
        await cleanup()

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--url-match", help="substring to identify the target tab (attach mode)")
    parser.add_argument(
        "--launch", action="store_true", help="launch a new browser instead of attaching to one"
    )
    parser.add_argument("--url", help="URL to load in the launched browser (launch mode)")
    parser.add_argument(
        "--executable-path",
        type=Path,
        default=Path.home() / ".cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=Path.home() / ".local/share/maestro/browser-profile",
    )
    parser.add_argument(
        "--load-extension",
        type=Path,
        default=Path("/home/dyung/Projects/switchboard/dist"),
    )
    parser.add_argument(
        "--switchboard-account",
        help="name of the Switchboard account to switch to before capturing",
    )
    parser.add_argument(
        "--keep-bottom-overlays",
        action="store_true",
        help="don't hide fixed/sticky elements docked to the bottom of the viewport (e.g. a chat composer) before capturing",
    )
    parser.add_argument(
        "--keep-top-overlays",
        action="store_true",
        help="don't hide fixed/sticky elements docked to the top of the viewport (e.g. a header) on every shot but the first",
    )
    parser.add_argument(
        "--max-stitch-height",
        type=int,
        default=16000,
        help="roll over into a new stitched_NNN.png whenever a stitched image would exceed this height in pixels",
    )
    parser.add_argument(
        "--scroll-overlap",
        type=int,
        default=150,
        help="pixels of intentional re-capture between consecutive shots, giving stitch() real duplicate content to detect and crop instead of advancing by exactly one viewport with no redundancy",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="print each scroll step to a PDF page and merge them, instead of stitching screenshots into a PNG",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="extract the page's text instead of capturing screenshots or PDF pages, reading whatever is visible in the viewport at each scroll step and stitching the results together, the same way screenshots are captured",
    )
    expand_group = parser.add_mutually_exclusive_group()
    expand_group.add_argument(
        "--expand-only",
        action="store_true",
        help="scroll through the page expanding collapsible content, but skip taking screenshots or PDF pages",
    )
    expand_group.add_argument(
        "--skip-expand",
        action="store_true",
        help="skip expanding collapsible content and go straight to capturing screenshots or PDF pages",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="launch the browser headless (launch mode only) - the reliable path for --pdf, since Chrome's print-to-PDF is unreliable in a headful window",
    )
    parser.add_argument(
        "--max-pdf-pages",
        type=int,
        default=90,
        help="roll over into a new merged_NNN.pdf whenever the next step's pages would push the current file past this many pages",
    )
    parser.add_argument(
        "--start-position",
        action="store_true",
        help="skip the initial scroll-to-top and instead wait for you to position the page yourself, then press Enter to begin capturing from there",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="close the browser immediately after capture instead of waiting for confirmation",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("./capture_output"))
    parser.add_argument("--settle-ms", type=int, default=150)
    parser.add_argument("--settle-checks", type=int, default=2)
    parser.add_argument("--settle-interval-ms", type=int, default=200)
    args = parser.parse_args()

    if args.launch and not args.url:
        parser.error("--url is required when --launch is set")
    if not args.launch and not args.url_match:
        parser.error("--url-match is required when attaching to an existing browser")
    if args.pdf and args.text:
        parser.error("--pdf and --text are mutually exclusive")
    if args.text and args.expand_only:
        parser.error("--text and --expand-only are mutually exclusive")

    return args


def main() -> None:
    args = parse_args()
    results = asyncio.run(run(args))
    if not results:
        print("Expansion complete.")
    elif results[0].suffix == ".txt":
        print(f"Text: {results[0]}")
    elif len(results) == 1:
        print(f"Stitched screenshot: {results[0]}" if not results[0].suffix == ".pdf" else f"PDF: {results[0]}")
    else:
        label = "Stitched screenshot" if results[0].suffix != ".pdf" else "PDF"
        print(f"{label} into {len(results)} parts:")
        for path in results:
            print(f"  {path}")


if __name__ == "__main__":
    main()
