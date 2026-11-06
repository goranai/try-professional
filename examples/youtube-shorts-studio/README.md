# YouTube Shorts studio

A whole company for one goal, and the files it produces. This is the project's flagship example.

**What it produces:** research, scripts, storyboards, edit notes, thumbnail briefs, titles, descriptions, and an upload schedule, as files.

**What it doesn't do:** render video, make images, upload anything, or read your channel's data. A person does those. Connectors for real-world actions may come later, as options, always gated by your approval.

## Try it with no key or cost

A scripted model stands in, so this shows how the company works, not what a model would write.

```bash
pip install try-professional
mkdir my-studio && cd my-studio
trypro init --template youtube-shorts-studio --no-discover
```

That ends at the review screen. Type `a`, then `approve`. Then:

```bash
mkdir inputs
cp path/to/examples/youtube-shorts-studio/inputs/sample-notes.md inputs/
trypro run --dry-run "Make 5 Shorts about AI tools for this week"
```

Open `deliverables/<run-id>/index.md`.

## With a real model

Set these in `.env`:

```bash
TRYPRO_LLM_BASE_URL=https://your-provider.example/v1
TRYPRO_LLM_API_KEY=your-key
TRYPRO_LLM_MODEL=your-model
GITHUB_TOKEN=your-token        # optional: lets init search GitHub for skills
```

```bash
trypro init "a YouTube Shorts content company"
trypro run "Make 5 Shorts about AI tools for this week"
```

`trypro init` designs the company, finds skills, and shows you everything before anything is activated. It tells you what it spent, and estimates a typical run.

## "This week" needs a source

Roles can't look anything up by themselves. Unless you give them current information, they only have the model's training data, which could be out of date. So:

- Put your own notes, exports, or research in `inputs/`. Every run gets a copy, and can cite it.
- Or turn on web search in `trypro.yaml`. It's off by default, and has its own key.
- A research finding that calls something current or trending needs a source and a date. Without them, it's labelled **UNVERIFIED** and can't be called this week's trend.
- `index.md` marks each citation as retrieved during the run, or not, because a model can invent a source too.

`inputs/sample-notes.md` is a made-up sample to show the format. It isn't research, so don't rely on anything in it.

## What to check before publishing

The company's Legal and QA reviews are checks by agents, not legal advice. Before you publish, check that:

- Facts in each script are true.
- You have the rights to any music, footage, and images.
- Your use of AI follows the platform's current disclosure rules.
