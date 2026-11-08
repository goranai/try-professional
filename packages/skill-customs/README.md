# skill-customs

The lockfile, license, and pinning layer for agent skills.

Agent skills are folders of instructions, and sometimes scripts, that you download from strangers and give to an agent. `skill-customs` records exactly what you installed, from which commit, under which license, and how far you trust it, the way a lockfile does for code. It works with any agent framework and has one dependency, PyYAML.

**The scanner is a tripwire for importing third-party skills, not a full audit.** Its verdicts are for a person to read, not for anything to obey. It has been run on about 1,450 skills, on Linux, and [what that found](https://github.com/goranai/try-professional/blob/main/docs/research/scanner/README.md) shaped 0.1.1. It doesn't compete on scanning: a static scanner ships as the default, and other scanners plug in. Use the best one you can find.

```text
GitHub ─► fetch by full SHA ─► license gate ─► scan ─► skills.lock
                                    │             │
                            rejected: nothing   clean: skills/installed/   (indexed)
                            is installed        else:  skills/quarantine/  (until a person approves)
```

## Try it in under 5 minutes

```bash
pip install skill-customs

# Scan and license-check any skill folder. Nothing needs to be set up.
skill-customs scan ./my-skill        # exit 0 pass, 1 review, 2 block
skill-customs license ./my-skill

# Pin a skill. Branches and tags aren't accepted, so get a full SHA first.
skill-customs resolve obra/superpowers@main
skill-customs add obra/superpowers --commit <40-character-sha> --path skills/test-driven-development

skill-customs list                   # what's locked, and how far it's trusted
skill-customs verify                 # do the files still match skills.lock? Run this in CI.
skill-customs attribution --out THIRD_PARTY_SKILLS.md
```

Commit `skills.lock`. Point your agent at `skills/installed/`, which never contains anything quarantined.

Set `GITHUB_TOKEN` for higher rate limits. It's optional, read-only access to public repositories is enough, and it's never sent to any host but GitHub's API.

## What it guarantees, and what it doesn't

- **Nothing from a skill is ever run.** It downloads a tarball, so no git hooks can fire, and it writes files itself.
- **A pin is exact.** Only a full 40-character SHA is accepted, and a hash of the whole folder is recorded. `verify` catches any edited, added, or removed file.
- **Archives aren't trusted.** Links, path traversal, special files, and oversized content stop the install.
- **Quarantine is a real location.** A skill that isn't approved isn't in the folder your agent reads.
- **An approval covers one exact version.** Any change to the content voids it.

**The scanner is a tripwire, not an audit.** It reads text for known signs of an attack, in five languages, including text hidden in base64 or hex, split over lines, or written with look-alike letters. A block or a review is a reason for a person to look, and a pass means only that no known sign was found. Headings, tables, quoted examples, and a description in frontmatter can lower a block to a review, and never lower it further. A skill's name only ever gets a note.

**A passing scan doesn't mean a skill is safe.** It means no known signal was found. Static rules can be bypassed by rephrasing, and they will sometimes flag innocent text: a skill that teaches prompt writing may well quote the phrases the rules look for. That's why `indexed` skills should still be treated as untrusted data, and why your runtime should limit what an agent can do whatever a skill tells it. Scanning reduces risk, and doesn't remove it.

## Trust levels

| Level | Meaning | Where it lives |
|---|---|---|
| `indexed` | Passed automated checks. Instructions only, with no scripts or findings | `installed/` |
| `quarantined` | Has scripts or findings, or was written locally. Waiting for a person | `quarantine/` |
| `verified` | A person reviewed this exact content | `installed/` |

To approve a skill, read its files, then run this at a terminal:

```bash
skill-customs approve owner/repo/skills/name --by "Your Name"
```

The command refuses to run without a terminal, and asks you to type the skill ID back, so it can't be casually run by a script or an agent. It isn't a hard security boundary: the Python function exists, and code with write access to your project can do anything. **Don't expose approval as an agent tool.**

## Licenses

Only these are accepted by default: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC. To change that, add `skill-customs.yaml`:

```yaml
allowed_licenses: [MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, MPL-2.0]
```

It reads the skill's declared license, its own license file, and the repository's license file. An unknown license is rejected. If the sources disagree, the stricter reading wins, so a skill can't declare MIT inside a GPL repository. Non-commercial and proprietary terms are flagged. This is an automated check of license text, and isn't legal advice.

## Use it from Python

```python
from pathlib import Path
from skill_customs import GitHubClient, LicenseRejected, Lockfile, Policy, check_license, pin, scan, verify

report = scan(Path("./my-skill"))
print(report.verdict)  # pass, review, or block

policy = Policy.default()
print(check_license(Path("./my-skill"), policy).allowed)

lock = Lockfile.load("skills.lock")
commit = GitHubClient().resolve_ref("obra/superpowers", "main")
try:
    result = pin("obra/superpowers", commit, path="skills/test-driven-development", dest=Path("skills"), policy=policy)
    lock.add(result.entry).save()
except LicenseRejected as error:
    print(error.verdict.reason)

assert verify(lock, dest=Path("skills")) == []
```

## Plug in another scanner

Any of these run alongside the built-in one, and a scanner that crashes counts as a blocking finding.

**An external command.** Exit 0 means clean, and anything else sends the skill to a person. Its output isn't interpreted.

```yaml
# skill-customs.yaml
command_scanners:
  my-scanner: [my-scanner, scan, --strict]   # the skill's folder is added as the last argument
```

**A Python class:**

```python
from pathlib import Path
from skill_customs import Finding, scan


class MyScanner:
    name = "my-scanner"

    def scan(self, root: Path) -> list[Finding]:
        return [Finding("my-rule", "review", "SKILL.md", 1, "Why this needs a look", scanner=self.name)]


report = scan(Path("./my-skill"), scanners=[MyScanner()])
```

**An installed package,** through the `skill_customs.scanners` entry point group. It runs only if `scanners:` in the policy names it, so installing a package doesn't turn anything on.

## Use it with your framework

It needs nothing from your framework, and your framework needs only a folder and `skills.lock`.

**Any framework.** Read skills from `skills/installed/`, and treat the text as data rather than as trusted instructions. To read the lockfile:

```python
from pathlib import Path
from skill_customs import Lockfile, read_skill_metadata, skill_dir

lock = Lockfile.load("skills.lock")
for skill_id, entry in lock.entries.items():
    if entry.trust != "quarantined":
        folder = skill_dir(entry, Path("skills"))
        print(skill_id, entry.trust, read_skill_metadata(folder).description)
```

**Hermes Agent.** Add the installed folder as an external skill directory in `~/.hermes/config.yaml`. Hermes treats skills there as instructions, so consider listing a folder that holds only `verified` skills:

```yaml
skills:
  external_dirs:
    - /path/to/project/skills/installed
```

**MCP clients and CrewAI.** There's no packaged integration yet. The approach above works, and these are good first issues.

## In CI

```yaml
- run: pip install skill-customs
- run: skill-customs verify
- run: skill-customs attribution --check THIRD_PARTY_SKILLS.md
```

## Finding candidates

```bash
skill-customs discover "code review agent skills" --top 40
```

This ranks public repositories by stars, recent activity, and open issues, and installs nothing. Stars can be bought, so ranking only decides what to look at first, and every candidate still passes the same gates.

To refresh a whole list, map each category to a search query, and run it on your own schedule. There's no daemon.

```bash
skill-customs discover --categories categories.yaml --out candidates.json
```

## Status

Pre-release. Part of the [Try Professional](https://github.com/goranai/try-professional) repository, and usable entirely without it.

MIT licensed.
