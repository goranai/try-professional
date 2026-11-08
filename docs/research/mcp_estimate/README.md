# How much an MCP client really spends, against the server's count

Over MCP, the server can't see what a client spends, and can't trust what it says. So it counts what passes through it: the instructions and assignment it hands a role, and what the role hands back, at about 3.5 characters per token. That misses the client's own instructions, its thinking, and its tool calls. **From 0.1.2, the server multiplies its count by 3.3**, and limits over MCP use that number.

## The factor

We compared the server's count with Claude Code's own usage, on every command where both were recorded: 17 commands, all on `claude-sonnet-5-5`.

- **Fresh tokens** are what the model read for the first time or wrote: input, cache writes, and output. They leave out cache reads, which are Claude Code re-reading the same instructions on every turn of every process. Cache reads cost about a tenth as much, and they'd swamp the comparison.
- **Fresh tokens were 3.3 times the server's count**, at the median, from 2.3 to 4.6. Commands with more steps, like the Shorts batches, ran lower. The two acceptance commands, run through one `/company` session, ran highest, because that session's own coordinating adds to every command.

| | Fresh tokens ÷ the server's count |
|---|---|
| Median, of 17 | 3.28 |
| Lowest | 2.32, the second Shorts batch |
| Highest | 4.63, the first acceptance command |

`runs.json` has every command: the server's count, fresh tokens, and fresh tokens with cache reads. With cache reads, the median is 13 times.

## What it changes

- **The limits over MCP are in corrected tokens.** 150,000 for a small command and 500,000 for a large one, by default. Every one of these 17 commands would have finished under them. Corrected, the most a small one came to was 124,615, and a large one 443,154: the second Shorts batch, whose real ratio was only 2.3, so the correction overcounts it. 500,000 leaves room for one more round of rework on a command like that.
- **The API runner keeps 60,000 and 200,000.** It counts real tokens from the provider, and needs no correction. Higher limits there would only let a stuck run on a cheap model spend more.
- **Stuck-run detection comes first on both.** A run stops as stuck after 24 model calls with nothing moving, 3 refused hand-ins, or a whole command's limit of tokens with nothing moving, whatever budget a person added. [How that was measured](../stuck/README.md).

## What it doesn't show

- **One client, and one model.** These are all Claude Code on Sonnet. Another client, or another model, could spend a different multiple.
- **It's an average.** A command can spend more or less than 3.3 times the count, so a limit over MCP is still an estimate. Claude Code's own usage limits apply too.
