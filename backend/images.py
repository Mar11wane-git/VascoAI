"""
AI images through a small server-side proxy (/api/image), used by generate_image and create_video.

- With POLLINATIONS_API_KEY (free account at enter.pollinations.ai): fast images from the keyed API, the key
  never leaves the server.
- Without a key, or when the keyed API fails (pollen used up, key revoked, outage): the anonymous Pollinations
  endpoint (about one image every 45 s per IP); the proxy waits and retries when told to.

Image URLs are signed, so the proxy cannot be used by others to spend the key on arbitrary prompts.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import urllib.parse
from typing import Tuple

import httpx

MODEL = os.getenv("POLLINATIONS_IMAGE_MODEL", "flux")
log = logging.getLogger("vasco.images")


class ImageError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _key() -> str:
    return os.getenv("POLLINATIONS_API_KEY", "").strip()


def sign(prompt: str, width: int, height: int, seed: int) -> str:
    secret = hashlib.sha256((_key() or "vasco-anonymous").encode() + b"/vasco-image").digest()
    msg = f"{prompt}|{width}|{height}|{seed}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def verify(prompt: str, width: int, height: int, seed: int, sig: str) -> bool:
    return hmac.compare_digest(sign(prompt, width, height, seed), sig or "")


def image_url(prompt: str, width: int, height: int, seed: int) -> str:
    prompt = prompt.strip()[:1000]
    q = urllib.parse.urlencode({"p": prompt, "w": width, "h": height, "s": seed,
                                "sig": sign(prompt, width, height, seed)})
    return f"/api/image?{q}"


async def fetch_image(prompt: str, width: int, height: int, seed: int) -> Tuple[bytes, str]:
    quoted = urllib.parse.quote(prompt)
    key = _key()
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        if key:
            try:
                r = await client.get(
                    f"https://gen.pollinations.ai/image/{quoted}",
                    params={"model": MODEL, "width": width, "height": height, "seed": seed, "nologo": "true"},
                    headers={"Authorization": f"Bearer {key}"},
                )
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    return r.content, r.headers["content-type"]
                # Out of pollen, revoked key, outage…: fall back to the anonymous endpoint (slower, still free).
                log.warning("Keyed Pollinations image failed (%s): %s — falling back to anonymous",
                            r.status_code, r.text[:120])
            except httpx.HTTPError as e:
                log.warning("Keyed Pollinations image error (%s) — falling back to anonymous", e)

        url = (f"https://image.pollinations.ai/prompt/{quoted}"
               f"?width={width}&height={height}&seed={seed}&nologo=true")
        for _ in range(3):
            r = await client.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content, r.headers["content-type"]
            m = re.search(r"Retry after (\d+)s", r.text)
            if r.status_code in (402, 429) and m:  # anonymous limit: wait the time the service asks for
                await asyncio.sleep(min(int(m.group(1)) + 1, 50))
                continue
            break
        raise ImageError(f"Pollinations {r.status_code}: {r.text[:200]}", 429 if r.status_code in (402, 429) else 502)
