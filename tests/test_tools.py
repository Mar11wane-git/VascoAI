"""Agent tools and helpers that need no network."""
import asyncio
import urllib.parse

from backend import images
from backend.agent.tools import execute_tool


def run(coro):
    return asyncio.run(coro)


def test_calculator():
    out = run(execute_tool("calculator", {"expression": "17*23"}, ["calculator"]))
    assert out.ok and out.content.endswith("= 391")


def test_calculator_percent_and_errors():
    assert run(execute_tool("calculator", {"expression": "20% of 150".replace(" of ", "*")}, ["calculator"])).ok
    assert not run(execute_tool("calculator", {"expression": "1/0"}, ["calculator"])).ok
    assert not run(execute_tool("calculator", {"expression": "__import__('os')"}, ["calculator"])).ok


def test_disallowed_tool_never_runs():
    out = run(execute_tool("run_python", {"code": "print(1)"}, ["calculator"]))
    assert not out.ok and "not available" in out.content


def test_invalid_json_arguments():
    out = run(execute_tool("calculator", "{not json", ["calculator"]))
    assert not out.ok and "Invalid arguments" in out.content


def test_image_links_are_signed():
    url = images.image_url("a monkey selling bananas", 720, 1280, 42)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
    assert url.startswith("/api/image?")
    assert images.verify(q["p"], int(q["w"]), int(q["h"]), int(q["s"]), q["sig"])
    # Any change to the prompt, size or seed invalidates the signature (nobody can spend the key on other images).
    assert not images.verify(q["p"] + "!", int(q["w"]), int(q["h"]), int(q["s"]), q["sig"])
    assert not images.verify(q["p"], 1024, int(q["h"]), int(q["s"]), q["sig"])
    assert not images.verify(q["p"], int(q["w"]), int(q["h"]), 43, q["sig"])
    assert not images.verify(q["p"], int(q["w"]), int(q["h"]), int(q["s"]), "")


def test_create_video_builds_a_reel_spec():
    scenes = [{"narration": f"Scene {i}", "image_prompt": f"monkey {i}"} for i in range(10)]
    out = run(execute_tool("create_video", {"title": "Bananito", "scenes": scenes, "aspect": "16:9",
                                            "character": "a small monkey"}, ["create_video"]))
    assert out.ok
    v = out.video
    assert (v["width"], v["height"]) == (1280, 720)
    assert len(v["scenes"]) == 8  # capped
    assert all(s["image"].startswith("/api/image?") for s in v["scenes"])
    assert "a+small+monkey" in v["scenes"][0]["image"] or "a%20small%20monkey" in v["scenes"][0]["image"]


def test_create_video_needs_two_scenes():
    out = run(execute_tool("create_video", {"scenes": [{"narration": "x", "image_prompt": "y"}]}, ["create_video"]))
    assert not out.ok


def test_create_video_accepts_scenes_as_json_string():
    import json
    scenes = json.dumps([{"narration": "a", "image_prompt": "b"}, {"narration": "c", "image_prompt": "d"}])
    out = run(execute_tool("create_video", {"scenes": scenes}, ["create_video"]))
    assert out.ok and out.video["aspect"] == "9:16"
