"""Build tests/data/model_replies.json from real cached Opus 5 output.

These are the actual strings a model produced. They are the input `parse_answer` takes in
production, and until now nothing in the suite fed it one - every test built a T1Answer in
Python and serialised it, which cannot be wrong in the way real output is.
"""
import json
import pathlib
import sys

sys.path.insert(0, "/home/jak/redtape")

from eval.cache import CACHE_DIR  # noqa: E402
from redtape.scoring.parsing import parse_answer  # noqa: E402

OUT = pathlib.Path("/home/jak/redtape/tests/data/model_replies.json")

valid, abstaining, empty, other = [], [], [], []

for f in sorted(CACHE_DIR.rglob("*.json")):
    rec = json.loads(f.read_text(encoding="utf-8"))
    if rec.get("model") != "claude-opus-5":
        continue
    reply = rec["reply"]
    r = parse_answer(reply)
    if not r.ok:
        if not reply.strip():
            empty.append(reply)
        else:
            other.append(reply)
        continue
    try:
        obj = json.loads(reply)
    except json.JSONDecodeError:
        continue
    has_null = (obj.get("snap", {}).get("benefit") is None
                or obj.get("eitc", {}).get("amount") is None
                or obj.get("ctc", {}).get("amount") is None)
    (abstaining if has_null else valid).append(reply)

payload = {
    "note": (
        "Real Claude Opus 5 replies, captured verbatim from the committed response cache. "
        "Fixtures for the prompt->model->JSON->parse path, which had no test coverage until "
        "2026-09-04 - see docs/LIMITS.md 25 and 27. Do not hand-edit: regenerate with "
        "scripts/build_reply_fixtures.py so they stay real output rather than what we "
        "imagine real output looks like."
    ),
    "model": "claude-opus-5",
    "valid": valid[:40],
    "abstaining_with_null": abstaining[:25],
    "empty": empty[:3],
    "other_rejected": other[:10],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

print(f"valid                : {len(valid)}  (kept {len(payload['valid'])})")
print(f"abstaining with null : {len(abstaining)}  (kept {len(payload['abstaining_with_null'])})")
print(f"empty                : {len(empty)}  (kept {len(payload['empty'])})")
print(f"other rejected       : {len(other)}  (kept {len(payload['other_rejected'])})")
print(f"wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")
