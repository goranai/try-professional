# Security

**Please report a vulnerability privately:** use [GitHub's private vulnerability reporting](https://github.com/goranai/try-professional/security/advisories/new), not a public issue. This is a preview maintained by a small team, so expect a reply within about a week.

## What counts

Try Professional runs third-party skill text and model output, so these are the things that matter most:

- A skill or a model's output gets a role a tool, memory, or file it wasn't given.
- A role reads another role's personal memory, or a bound MCP role sees the rest of the company.
- Work reaches a person without passing a review gate that applied to it.
- A client can approve a company or a skill, or answer for a person.
- A key is logged or written to a file.
- Something gets past `skill-customs` that its scanners or license checks should have stopped.

## What it doesn't protect against

- **A model doing poor work.** Code holds the rules, but a review by a weak model can still approve weak work.
- **A client's own tools.** Over MCP, a client's shell or file editor is outside this server's control, unless the client turns them off. `trypro agents claude-code` does that for each role, but not for your own session. Approving and answering a run's questions refuse anything but a real terminal, and a session with an open shell could still change your files, including the company's records. Keep Claude Code's permission prompts on.
- **Operator mode over MCP.** One model plays every role, so separation between roles is only procedural.
- **Scanners catching everything.** A scan lowers risk. It doesn't prove a skill is safe. Read what you approve.
