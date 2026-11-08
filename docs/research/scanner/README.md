# The scanner, before and after 0.1.1

An external test ran `skill-customs` 0.1.0 over 1,449 skills from a private library, on Linux, in about 86 seconds: 1,108 passed, 210 were blocked, and 131 needed review. A person read the findings, and found four kinds of false alarm and two kinds of miss. **0.1.1 fixes those six, and one more found while fixing them.**

## How it was measured

Nothing from that library is here. `packages/skill-customs/tests/scanner_cases.py` has 29 small skills written for this. Each pattern is there as an attack where there was one, and as a harmless look-alike, with the verdict a person would want. The six true positives the external test found are there too, and must stay caught. `measure.py` runs a given version of the scanner over them.

**Read the "after" column for what it is.** The set was written while fixing these patterns, so the fixed scanner getting every case right is expected. It shows each fix works, and that the earlier catches still hold. It doesn't show how the scanner does on skills it wasn't fixed for. Rerunning the same 1,449 skills would show that.

## Verdicts

| | 0.1.0 | 0.1.1 |
|---|---|---|
| Verdicts as a person would want them, of 29 | 18 | 29 |

| Case | 0.1.0 | 0.1.1 |
|---|---|---|
| A frontmatter name taken from an attack technique, on a harmless skill | review | pass, with a note |
| "Ignore all previous instructions" in a frontmatter description | block | review |
| `curl … \| python3 -m json.tool` | block | pass |
| A heading about sending test events to webhook.site | block | review |
| A threat catalog's table row, quoting an override | block | review |
| An override quoted as an example, in prose | block | review |
| Base64 in a comment, hiding a credential theft, and a line saying to decode it | pass | block |
| The same, in hex | pass | block |
| A request to send a file to a URL, politely split over 3 lines | pass | block |
| An override split over 3 lines | pass | block |
| "Ignore all previous instructions", with two Cyrillic look-alike letters | pass | block |

**The last one wasn't caught before.** 0.1.0 had no rule for look-alike letters, so a Cyrillic "о" in "ignore" hid it completely. The external scan's catch of homoglyphs must have come from something else on those lines.

## Per rule

A rule counts as firing at any severity, so a block that should have been a review is a verdict error, above, and not a precision error here.

| Rule | 0.1.0 precision | 0.1.0 recall | 0.1.1 precision | 0.1.1 recall |
|---|---|---|---|---|
| `override-en` | 1.00 | 0.67 | 1.00 | 1.00 |
| `override-persona-en` | 0.67 | 1.00 | 1.00 | 1.00 |
| `remote-pipe` | 0.75 | 1.00 | 1.00 | 1.00 |
| `exfil-en` | 1.00 | 0.40 | 1.00 | 1.00 |
| `exfil-host` | 1.00 | 1.00 | 1.00 | 1.00 |
| `credentials-en` | 1.00 | 0.33 | 1.00 | 1.00 |
| `mixed-script`, new | | | 1.00 | 1.00 |
| `exfil-curl`, `conceal-en`, `credentials-command` | 1.00 | 1.00 | 1.00 | 1.00 |

## What changed

- **A frontmatter name is a label.** A rule that matches it is recorded as a note, which never changes a verdict. A **description** still counts, at review rather than block, because an agent reads descriptions to choose a skill.
- **Headings, tables, block quotes, reference lists, and quoted examples** turn a block into a review. A person still reads it. Code blocks never get softened, because that's where a real install command is.
- **`remote-pipe`** fires on a pipe into something that runs what it reads: a shell, `python` or `python -`, `node`, `perl`, `ruby`, `php`, or PowerShell. Not `python -m json.tool`, `jq`, or other formatters.
- **Base64 and hex** of at least 24 and 32 characters are decoded, and if they're readable text, it's scanned like any other. Hashes and binary aren't text, and are skipped.
- **The behaviour rules** for override, conceal, exfiltration, and credentials also run over up to 3 lines of one paragraph.
- **Look-alike letters** from Cyrillic and Greek are folded to Latin inside a word that mixes alphabets, and the word itself is flagged for review. Text that's all Russian or Greek isn't touched.

## 0.1.2

The same tester rescanned the same 1,449 skills with 0.1.1: 1,075 passed, 161 got a review, and 213 were blocked. Most changes were the fixes working. Three things weren't:

- **28 skills went from pass to block,** all but two of them on `exfil-curl` across lines in code. Reading 3 lines together joined a request's own `-d '{...}'` body, or the next command, into what looked like an upload.
- **Mixed scripts made 31 findings, in 11 skills.** At least 12 findings were maths or units, a Greek Δ, μ, σ, or π next to Latin letters. The rest were real look-alike words, in skills that teach about them, or cut from the excerpt.
- **One probe still passed:** a secret's location, put into the agent's reply, and kept out of its summary, over 3 lines, with no URL.

### What changed

- **Code is read by command.** A command continued with `\`, `` ` ``, or `^` is one line, and command rules don't read across anything else in code. Only override and conceal rules still read across lines in code, for an instruction split over comments. A script, such as `run.sh`, is code throughout.
- **`exfil-curl` needs something that isn't written out:** a file, a variable, or a command's output. In code, it blocks only when that's a secret, and is a review otherwise. Outside code, it still blocks.
- **`mixed-script` and the folding of look-alikes** need a letter that passes for Latin, in one word split at digits and underscores.
- **`exfil-reply` and `conceal-secret` are new,** for a secret's location put into the reply, or kept from the person. A warning, such as "never include", doesn't count.

### Measured

`scanner_cases.py` now has 59 cases: the 29 from 0.1.1, 23 more for these patterns, and the tester's own 7 synthetic probes, with one name made neutral. **Read the "after" column for what it is:** the new cases were written while fixing these patterns.

| | 0.1.1 | 0.1.2 |
|---|---|---|
| Verdicts as a person would want them, of 59 | 48 | 59 |

| Rule | 0.1.1 precision | 0.1.1 recall | 0.1.2 precision | 0.1.2 recall |
|---|---|---|---|---|
| `exfil-curl` | 0.70 | 0.88 | 1.00 | 1.00 |
| `mixed-script` | 0.67 | 1.00 | 1.00 | 1.00 |
| `exfil-reply`, new | | 0.00 | 1.00 | 1.00 |
| `conceal-secret`, new | | 0.00 | 1.00 | 1.00 |
| Every other rule that fired | 1.00 | 1.00 | 1.00 | 1.00 |

"0.00" recall for a new rule means 0.1.1 had no rule for those cases. `results_0.1.2.json` has every case, for both versions.

### The library itself

The library isn't here, and nothing from it is. `compare_library.py` rescans it where it lives, and prints only totals, transitions, and rule counts.

## Reproduce

```bash
python docs/research/scanner/measure.py <path to skill_customs source> <label> results.json
```

`results.json` has every case, for 0.1.0 and 0.1.1, on the first 29 cases. `results_0.1.2.json` has 0.1.1 and 0.1.2, on all 59.
