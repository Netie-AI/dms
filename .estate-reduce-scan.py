import subprocess, re, os, sys

REPO = r"D:\DMS"

def run(*args):
    r = subprocess.run(args, capture_output=True, text=True, cwd=REPO)
    return r.stdout.strip() if r.returncode == 0 else ""

def is_ancestor(sha):
    r = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=REPO)
    return r.returncode == 0

main = run("git", "rev-parse", "origin/main")
porcelain = run("git", "worktree", "list", "--porcelain")
blocks = re.split(r"\n(?=worktree )", porcelain.strip())
worktrees = []
for b in blocks:
    lines = b.splitlines()
    d = {}
    for ln in lines:
        if ln.startswith("worktree "):
            d["path"] = ln[9:]
        elif ln.startswith("HEAD "):
            d["head"] = ln[5:]
        elif ln.startswith("branch "):
            d["branch"] = ln[7:].replace("refs/heads/", "")
        elif ln == "detached":
            d["branch"] = "detached"
    worktrees.append(d)

print("=== WORKTREES ===")
for wt in worktrees:
    p = wt["path"]
    head = wt["head"]
    branch = wt.get("branch", "detached")
    st = subprocess.run(
        ["git", "-c", f"safe.directory={p}", "-C", p, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    dirty = "Y" if st.stdout.strip() else "N"
    counts = run("git", "rev-list", "--left-right", "--count", f"origin/main...{head}")
    behind, ahead = counts.split() if counts else ("?", "?")
    merged = "Y" if is_ancestor(head) else "N"
    subj = run("git", "log", "-1", "--format=%s", head)
    print(f"WT|{p}|{branch}|{dirty}|{ahead}|{behind}|{merged}|{head}|{subj}")

print("=== REMOTE BRANCHES ===")
remotes = run("git", "for-each-ref", "--format=%(refname:short)|%(objectname)", "refs/remotes/origin").splitlines()
for line in remotes:
    if not line or line.endswith("/HEAD"):
        continue
    name, sha = line.split("|", 1)
    if name == "origin/main":
        continue
    counts = run("git", "rev-list", "--left-right", "--count", f"origin/main...{sha}")
    behind, ahead = counts.split() if counts else ("?", "?")
    merged = "Y" if is_ancestor(sha) else "N"
    subj = run("git", "log", "-1", "--format=%s", sha)
    print(f"RM|{name}|{merged}|{ahead}|{behind}|{sha}|{subj}")
