"""One-time: copy a local harness's results/ into this checkout's results/ for the first CI run, so coverage, decks,
resident state and what's installed carry over instead of starting fresh. Screenshots and logs are left behind;
every string is scrubbed (account names, then absolute paths made relative to the harness).

  python3 ci/seed.py /path/to/old/harness        # then: RUNNER_TEMP=$(mktemp -d) ci/results.sh save "seed"
"""
import json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(WS, "soak"))
import runner as R

SKIP_DIRS = {"evidence"}
SKIP_EXT = {".png", ".log"}


def clean(v, old_ws):
    if isinstance(v, str):
        return R.scrub(v.replace(old_ws + "/", ""))
    if isinstance(v, list):
        return [clean(x, old_ws) for x in v]
    if isinstance(v, dict):
        return {k: clean(x, old_ws) for k, x in v.items()}
    return v


def main(old):
    old = os.path.abspath(old)
    src, dst = os.path.join(old, "results"), os.path.join(WS, "results")
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1] in SKIP_EXT:
                continue
            p = os.path.join(root, f)
            out = os.path.join(dst, os.path.relpath(p, src))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if f.endswith(".json"):
                json.dump(clean(json.load(open(p)), old), open(out, "w"), indent=1)
            elif f.endswith((".jsonl",)):
                with open(out, "w") as o:
                    for line in open(p):
                        o.write(json.dumps(clean(json.loads(line), old)) + "\n")
            elif f.endswith(".md"):
                open(out, "w").write(clean(open(p).read(), old))
            else:
                shutil.copy2(p, out)
            n += 1
    print(f"seeded {n} files into {dst}")


if __name__ == "__main__":
    main(sys.argv[1])
