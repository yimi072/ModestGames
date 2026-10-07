import asyncio
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth

async def claim_game(cookies: list[dict], namespace: str, offer_id: str, slug: str) -> tuple[list[dict], str]:
    async with async_playwright() as p:
        # Prefer the runner's real Google Chrome over bundled Chromium: its
        # fingerprint is far less likely to trip Cloudflare's bot challenge.
        # ubuntu-latest runners preinstall Chrome; fall back if unavailable.
        launch_args = ["--disable-blink-features=AutomationControlled"]
        try:
            browser = await p.chromium.launch(channel="chrome", headless=False, args=launch_args)
        except Exception as e:
            print(f"Real Chrome unavailable ({e}); falling back to bundled Chromium")
            browser = await p.chromium.launch(headless=False, args=launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        await context.add_cookies(cookies)
        page = await context.new_page()
        # Mask automation fingerprints (navigator.webdriver, plugins, etc.)
        await Stealth().apply_stealth_async(page)
        
        purchase_url = f"https://store.epicgames.com/purchase?highlightColor=0078f2&lang=en-US&offers=1-{namespace}-{offer_id}--&showNavigation=true"

        async def snap(tag: str):
            """Save a debug screenshot (uploaded as Actions artifact)."""
            try:
                await page.screenshot(path=f"debug-screenshots/{slug}-{tag}.png")
            except Exception:
                pass

        try:
            # Warm up like a real user: homepage -> free games -> purchase.
            # This builds a natural navigation chain and lets any Cloudflare
            # clearance settle before the purchase page loads.
            for warm_url in ("https://store.epicgames.com/",
                             "https://store.epicgames.com/free-games"):
                try:
                    await page.goto(warm_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                except Exception as e:
                    print(f"Warm-up navigation to {warm_url} had issues: {type(e).__name__}")

            # The purchase page is a heavy SPA (analytics, streaming connections);
            # "networkidle" often never fires. Load DOM then poll for known states.
            await page.goto(purchase_url, wait_until="domcontentloaded", timeout=60000)

            clicked = False
            for _ in range(60):
                await asyncio.sleep(1)

                # Check for captcha
                if await page.locator(".h-captcha").count() > 0 or await page.locator("iframe[src*='hcaptcha']").count() > 0:
                    print(f"Captcha detected; title={await page.title()!r} url={page.url}")
                    await snap("captcha")
                    updated_cookies = await context.cookies()
                    return updated_cookies, "needs_captcha"

                # Check for success
                if "receipt" in page.url or await page.locator('[data-testid="receipt"]').count() > 0:
                    updated_cookies = await context.cookies()
                    return updated_cookies, "success"

                # Check if already owned
                try:
                    if await page.locator("text=You already own this").count() > 0:
                        updated_cookies = await context.cookies()
                        return updated_cookies, "already_owned"
                except Exception:
                    pass

                # Click Place Order once it appears
                if not clicked:
                    try:
                        place_order_btn = page.locator('button[data-testid="place-order-btn"]')
                        if await place_order_btn.count() == 0:
                            place_order_btn = page.locator('button:has-text("Place Order")')
                        if await place_order_btn.count() > 0:
                            await place_order_btn.first.click(timeout=5000)
                            clicked = True
                    except Exception:
                        pass

            # Timeout waiting for a recognizable state
            print(f"Timed out waiting for order state on {slug}; title={await page.title()!r} url={page.url}")
            await snap("timeout")
            updated_cookies = await context.cookies()
            return updated_cookies, "failed"
            
        except Exception as e:
            print(f"Exception during purchase flow: {e}")
            updated_cookies = await context.cookies()
            return updated_cookies, "failed"
        finally:
            await browser.close()
