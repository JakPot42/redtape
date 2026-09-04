"""Secret audit of a built wheel. Run before any publish.

Deliberately excludes digit runs that sit inside a hex digest. A 64-character SHA-256 has
~47 windows of 18 characters, each with a (10/16)^18 chance of being all digits, so across
1,200 task hashes a handful of 18-digit runs is EXPECTED. Flagging them as possible seed
material produces a detector that is wrong most times it fires, and a detector that cries
wolf is one people learn to wave through - which is how the schema_invalid signal in
docs/LIMITS.md 27 went misread for two days.
"""
import glob
import json
import re
import sys
import zipfile

wheels = sorted(glob.glob("/tmp/hubdist/*.whl"))
if not wheels:
    sys.exit("no wheel built")
w = wheels[-1]
z = zipfile.ZipFile(w)
names = z.namelist()
fail = []

print(f"auditing {w}\n")

# ---- 1. paths ---------------------------------------------------------------------------
BAD = re.compile(r"(^|/)\.env($|\.)|heldout|(^|/)cache/|\.pem$|\.key$|id_ed25519|id_rsa", re.I)
hits = [n for n in names if BAD.search(n)]
print(f"1. secret/held-out paths            : {len(hits)}")
for h in hits:
    print("     ***", h)
    fail.append(h)

# ---- 2. seed literals, excluding hex-digest context --------------------------------------
text = "".join(
    z.read(n).decode("utf-8", "ignore")
    for n in names
    if n.endswith((".py", ".json", ".jsonl", ".toml", ".md", ".txt"))
)
BENIGN = {"112233445566778899", "998877665544332211"}   # synthetic test fixtures
real = []
for m in re.finditer(r"(?<!\d)\d{18}(?!\d)", text):
    lit = m.group()
    if lit in BENIGN:
        continue
    # Widen to the surrounding token. If it is a long lowercase-hex run, it is a digest.
    lo = m.start()
    while lo > 0 and re.match(r"[0-9a-f]", text[lo - 1]):
        lo -= 1
    hi = m.end()
    while hi < len(text) and re.match(r"[0-9a-f]", text[hi]):
        hi += 1
    if hi - lo >= 32:
        continue          # inside a digest
    real.append(lit)
print(f"2. seed-shaped literals (non-digest): {len(real)}")
for r in sorted(set(real)):
    print("     ***", r)
    fail.append("seed literal")

# ---- 3. credentials ----------------------------------------------------------------------
creds = re.findall(r"sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY",
                   text)
print(f"3. credential-shaped strings        : {len(creds)}")
for c in creds[:5]:
    print("     ***", c[:24])
    fail.append("credential")

# ---- 4. seed-derived identifiers ----------------------------------------------------------
ids = set(re.findall(r"(?:hh|pair)-(\d{9,})-", text))
print(f"4. ids with a 9+ digit seed         : {len(ids)}")
for i in sorted(ids)[:5]:
    print("     ***", i)
    fail.append("heldout id")

# ---- 5. positive control: the public seed SHOULD be here ---------------------------------
pub = set(re.findall(r"(?:hh|pair)-(20260828)-", text))
seeds = set()
for n in names:
    if n.endswith(".jsonl"):
        for ln in z.read(n).decode("utf-8").splitlines():
            if ln.strip():
                seeds.add(json.loads(ln).get("seed"))
print(f"5. public dev seed present          : {'yes' if pub else 'NO'}  (expected: yes)")
print(f"   distinct seed values in data     : {seeds}  (expected: {{20260828}})")
if seeds - {20260828}:
    fail.append(f"unexpected seed values {seeds}")
if not pub:
    # Not a leak, but it means the positive control did not fire - the audit proved nothing.
    fail.append("positive control did not fire; audit is not trustworthy")

print()
if fail:
    print("AUDIT FAILED:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("AUDIT CLEAN - no secret material in the wheel, and the positive control fired.")
