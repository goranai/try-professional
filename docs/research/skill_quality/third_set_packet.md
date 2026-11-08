# Skills to label: who in this company would use each one?

These labels will measure how well an automatic matcher assigns skills to the roles in a company. Please label them before seeing any matcher's output, and independently.

## How to label

- A **skill** is a reusable instruction file an AI agent reads before doing a task. You only see its name and description below. Please don't look the skills up.
- For each skill, list every role below that would use it in its **own work**, judging by the role's mission and needs. Standard practice in a role's profession counts, even if its needs don't name it.
- Leave a role out if it would only see the skill's output, or if another role is clearly the one to use it.
- A skill about configuring an AI assistant itself, or for a trade or product that none of these roles covers, gets an empty list: `[]`.
- A skill with an empty description is labelled from its name.

## The company's 22 roles

| Role | Mission | Needs |
|---|---|---|
| `engineering/backend` | Build and maintain server-side features to spec. | design and build REST APIs; write database schemas and migrations; write server-side unit and integration tests; debug backend services |
| `engineering/devops` | Keep builds, deployments, and infrastructure reliable. | write CI and CD pipelines; write Dockerfiles and Kubernetes manifests; plan deployments and rollbacks; monitor services and respond to incidents |
| `engineering/frontend` | Build interfaces that work for real users. | build React user interfaces; write accessible HTML and CSS; manage frontend state; test components in the browser |
| `engineering/qa` | Decide whether engineering work meets its definition of done. | review code changes for bugs; design test plans and test cases; find security problems in code |
| `engineering/tech-lead` | Decide how to build it and split the work so it can be verified. | design software architecture; write technical design documents; plan a refactor; review code for design problems |
| `executive/ceo` | Own the goal from intake to final sign-off. | set company strategy and priorities; break a goal into a plan of work; make decisions between options; write an executive summary |
| `executive/chief-of-staff` | Keep the company on schedule and surface what is stuck. | track project status and blockers; run an escalation process; coordinate work between teams; write status reports |
| `finance/finance-lead` | Keep budgets honest and costs visible. | build budgets and financial forecasts; report on costs and spending; analyze pricing and margins; prepare invoices |
| `legal/compliance-reviewer` | Check work against the stated policies and obligations. | check work against compliance policies; audit personal data handling; write compliance checklists |
| `legal/counsel` | Catch license, terms-of-service, and privacy problems before they ship. | review contracts for risky clauses; check open source license compatibility; assess privacy and data protection obligations |
| `marketing/content-writer` | Write clear content for the intended audience. | write blog posts and articles; write website landing page copy; edit text for clarity and tone |
| `marketing/marketing-lead` | Decide the message and the channels. | plan marketing campaigns; define brand positioning and messaging; review marketing copy |
| `marketing/seo` | Help the right people find the content. | research search keywords; audit websites for SEO problems; write page titles and meta descriptions |
| `marketing/social` | Adapt the message for social channels. | write social media posts; plan a social media content calendar; adapt content for each social platform |
| `operations/ops-lead` | Make recurring work run smoothly. | write standard operating procedures; improve business processes; plan vendor and resource management |
| `operations/support` | Help customers solve their problems. | write customer support replies; write help center articles; triage customer tickets |
| `product/product-manager` | Turn goals into clear, testable product requirements. | write product requirements documents; prioritize a roadmap; define acceptance criteria and user stories |
| `product/ux-researcher` | Find out what users need and where they struggle. | plan user interviews; analyze usability test results; build user personas and journey maps |
| `research/analyst` | Find and analyze evidence. | analyze data and find trends; research competitors and markets; summarize academic papers and reports |
| `research/research-lead` | Turn questions into reliable answers. | frame research questions; review research methods and evidence quality; summarize findings for decision makers |
| `sales/proposal-writer` | Write proposals tailored to the client's needs. | write sales proposals and quotes; respond to requests for proposals; write case studies for prospects |
| `sales/sales-lead` | Decide who to sell to and how. | qualify sales leads; plan an outreach sequence; handle objections in sales conversations |

## The 57 skills

**S01. marketing-ideas**  
When the user needs marketing ideas, inspiration, or strategies for their SaaS or software product. Also use when the user asks for 'marketing ideas,' 'growth ideas,' 'how to market,' 'marketing strategies,' 'marketing tactics,' 'ways to promote,' 'ideas to grow,' 'what else can I try,' 'I don't know how to market this,' 'brainstorm marketing,' or 'what marketing should I do.' Use this as a starting point whenever someone is stuck or looking for inspiration on how to grow. For specific channel execution, see the relevant skill (ads, social, emails, etc.).

**S02. pricing**  
When the user wants help with pricing decisions, packaging, or monetization strategy. Also use when the user mentions 'pricing,' 'pricing tiers,' 'freemium,' 'free trial,' 'packaging,' 'price increase,' 'value metric,' 'Van Westendorp,' 'willingness to pay,' 'monetization,' 'how much should I charge,' 'my pricing is wrong,' 'pricing page,' 'annual vs monthly,' 'per seat pricing,' 'should I offer a free plan,' 'pricing page teardown,' 'pricing page audit,' 'is my pricing page AI-readable,' or 'can AI read my pricing.' Use this whenever someone is figuring out what to charge, how to structure their plans, or wants to audit a pricing page (for humans and for the AI agents that shortlist tools). For in-app upgrade screens, see paywalls. For offer construction (bonuses, guarantees, value framing, naming) on services/courses/coaching/high-ticket B2B, see offers.

**S03. 14-email-marketing-global**  
Use when the user needs email or lifecycle automation — welcome series, nurture, abandoned cart, re-engagement, broadcast, and transactional flows with subject lines, segmentation, cadence, and deliverability, plus legal variants: CAN-SPAM for US, GDPR opt-in for EU, PDPA for SEA, LGPD for LATAM. Covers Klaviyo, Mailchimp, and ActiveCampaign. Trigger on 'email marketing', 'welcome sequence', 'drip campaign', 'newsletter', 'abandoned cart emails', 'nobody opens our emails'. Not for — coding HTML that renders correctly in Outlook, see `49-html-email-template-global`; cold outbound to B2B prospects, see `33-b2b-lead-gen-global`; refer-a-friend loops, see `18-referral-program-global`.

**S04. 52-account-structure-global**  
Use when an ad account HIERARCHY needs designing or cleaning up — campaign, ad set, and ad structure by objective, a naming convention that keeps data readable, CBO versus ABO by stage, how many ad sets and creatives to run, and the minimum daily budget an ad set needs to exit learning. Trigger on 'account structure', 'campaign structure', 'naming convention', 'how many ad sets should I run', 'CBO or ABO', 'our account is a mess of duplicated campaigns'. Not for — scoring an existing account against 84 checkpoints, see `21-ads-audit-global`; pixel and conversion setup, see `53-tracking-setup-global`; budget across channels, see `54-media-plan-global`; scaling a winner, see `55-scaling-ads-global`.

**S05. 62-marketing-review-global**  
Use when a leader must review or approve TEXT-based team output before it ships — content briefs checked for angle, pillar, and CTA, ad copy and creative checked for substantiated claims, on-brand tone, a hook inside the first 125 characters, and platform policy, ending in an Approve, Revise, or Redo verdict with point-by-point feedback. Trigger on 'review this brief', 'approve the ad copy', 'check ads before launch', 'review team output', 'is this good enough to publish', 'my team keeps sending me drafts'. Not for — reviewing a visual design, see `47-design-review-global`; evaluating a person over a period, see `65-team-performance-review-global`; writing the copy yourself, see `05-ad-copy-global`.

**S06. sales-enablement**  
When the user wants to create sales collateral, pitch decks, one-pagers, objection handling docs, or demo scripts. Also use when the user mentions 'sales deck,' 'pitch deck,' 'one-pager,' 'leave-behind,' 'objection handling,' 'deal-specific ROI analysis,' 'demo script,' 'talk track,' 'sales playbook,' 'proposal template,' 'buyer persona card,' 'battle card,' 'battlecard,' 'competitive one-pager,' 'win-loss analysis,' 'why are we losing deals,' 'loss reasons,' 'how do I respond to this objection,' 'prospect just said,' 'help my sales team,' 'sales materials,' or 'what should I give my sales reps,' 'cold call script,' or 'voicemail script.' Use this for any document or asset that helps a sales team close deals. For public competitor comparison pages, see competitors. For running buyer interviews or churn research, see customer-research. For marketing website copy, see copywriting. For cold outreach emails, see cold-email. For the offer being sold (bonuses, guarantees, pricing structure), see offers.

**S07. 23-personal-brand-strategy-global**  
Use when a PERSONAL brand — founder, coach, or creator — needs a 12-month strategy: niche selection with Ikigai and a 3x3 matrix, positioning statement, a three-chapter story arc, four content pillars with 28 topics, a five-stage authority ladder, and a Q1 to Q4 growth plan. Trigger on 'personal brand strategy', 'founder brand plan', 'coach positioning', 'creator strategy', 'what niche should I own', 'I post constantly and nothing compounds'. Also use when the user has followers and no direction. Not for — a company brand, see `58-positioning-global` and `00-marketing-plan-global`; the context file this reads first, see `22-personal-brand-context-global`; turning it into revenue, see `27-personal-brand-monetize-global`.

**S08. cro**  
When the user wants to optimize, improve, or increase conversions on any marketing page or form — including homepage, landing pages, pricing pages, feature pages, lead capture forms, or contact forms. Also use when the user says 'CRO,' 'conversion rate optimization,' 'this page isn't converting,' 'improve conversions,' 'why isn't this page working,' 'my landing page sucks,' 'form abandonment,' 'nobody's converting,' 'low conversion rate,' or 'this page needs work.' Use this even if the user just shares a URL and asks for feedback. For signup/registration flows, see signup. For post-signup activation, see onboarding. For popups/modals, see popups.

**S09. rust-engineer**  
Writes, reviews, and debugs idiomatic Rust code with memory safety and zero-cost abstractions. Implements ownership patterns, manages lifetimes, designs trait hierarchies, builds async applications with tokio, and structures error handling with Result/Option. Use when building Rust applications, solving ownership or borrowing issues, designing trait-based APIs, implementing async/await concurrency, creating FFI bindings, or optimizing for performance and memory safety. Invoke for Rust, Cargo, ownership, borrowing, lifetimes, async Rust, tokio, zero-cost abstractions, memory safety, systems programming.

**S10. 67-agency-vendor-brief-global**  
Use when briefing or managing an EXTERNAL agency, freelancer, or production vendor — scope of work, deliverable specs, revision rounds, approval process, USD payment milestones, IP assignment and confidentiality terms, communication rules, and a post-project vendor scorecard. Trigger on 'brief an agency', 'hire a freelancer', 'scope of work', 'the agency is not delivering', 'onboard a new vendor', 'we keep paying for work we have to redo'. Also use when the user is about to sign a supplier with nothing written down. Not for — briefing an internal team member, see `64-team-brief-global`; the campaign brief itself, see `02-campaign-brief-global`; the asset list the vendor produces, see `41-campaign-asset-list-global`.

**S11. image**  
When the user wants to create, generate, edit, or optimize images for marketing — blog heroes, social graphics, product mockups, profile banners, listing visuals, or brand assets. Also use when the user mentions 'AI image generation,' 'generate an image,' 'create a graphic,' 'product mockup,' 'hero image,' 'social media graphic,' 'banner image,' 'cover photo,' 'profile banner,' 'listing screenshot,' 'Flux,' 'Flux Kontext,' 'Midjourney,' 'DALL-E,' 'GPT Image,' 'ChatGPT Images,' 'Ideogram,' 'Gemini image,' 'Nano Banana,' 'Recraft,' 'Stable Diffusion,' 'Canva,' 'Figma,' 'image optimization,' 'compress images,' 'WebP,' or 'OG image.' Use this for general-purpose marketing image creation and optimization. For paid ad image creative and platform-specific ad specs, see ad-creative. For video production, see video.

**S12. directory-submissions**  
When the user wants to submit their product to startup, SaaS, AI, agent, MCP, no-code, or review directories for backlinks, domain rating, and discovery. Also use when the user mentions "directory submissions," "submit to directories," "backlinks from directories," "list my product," "submit to Product Hunt," "BetaList," "TAAFT," "Futurepedia," "G2 listing," "Capterra listing," "AlternativeTo," "SaaSHub," "AI directories," "MCP registry," "publish my MCP server," "awesome list," "llms.txt directory," "Claude plugin directory," "agent directory," "dofollow backlinks," "launch directories," or "directory tracker." Use this whenever someone is planning the directory layer of a product launch or an ongoing backlink campaign. For the broader launch moment, see launch. For programmatic SEO pages that should live behind these backlinks, see programmatic-seo. For AI citation optimization, see ai-seo.

**S13. aso**  
When the user wants to audit or optimize an App Store or Google Play listing. Also use when the user mentions 'ASO audit,' 'app store optimization,' 'optimize my app listing,' 'improve app visibility,' 'app store ranking,' 'audit my listing,' 'why aren't people downloading my app,' 'improve my app conversion,' 'keyword optimization for app,' or 'compare my app to competitors.' Use when the user shares an App Store or Google Play URL and wants to improve it.

**S14. offers**  
When the user wants to design, construct, or improve an offer — the thing they actually sell — including value framing, bonus stacking, guarantee design, scarcity/urgency, naming, and payment structure. Also use when the user mentions 'offer,' 'offer design,' 'build an offer,' 'grand slam offer,' 'irresistible offer,' 'value stack,' 'bonus stack,' 'guarantee,' 'risk reversal,' 'money-back guarantee,' 'scarcity,' 'urgency,' 'high-ticket offer,' 'productize a service,' 'naming an offer,' 'payment plan,' 'down-sell,' 'upsell offer,' or 'why isn't my offer converting.' Best for services, agencies, courses, coaching, info products, high-ticket B2B, and direct-response. If you run pure self-serve SaaS, read pricing first — tiers and packaging do more work there. For price level itself (tiers, freemium, value metric), see pricing. For the page that presents the offer, see copywriting. For the launch moment, see launch. For sales collateral, see sales-enablement.

**S15. public-relations**  
When the user wants help with public relations, earned media, press coverage, journalist outreach, or media strategy (not pull requests). Also use when the user mentions 'PR,' 'press,' 'press release,' 'media outreach,' 'pitch a journalist,' 'get featured,' 'media list,' 'media kit,' 'press kit,' 'newsjacking,' 'news hijack,' 'HARO,' 'Qwoted,' 'Featured,' 'reporter request,' 'tech press,' 'TechCrunch,' 'thought leadership placement,' 'op-ed,' 'guest article,' 'press contacts,' 'podcast prep,' 'podcast guest,' 'prep me for this podcast,' 'how do I get press,' 'PR crisis,' 'crisis communications,' 'respond to backlash,' 'we got hacked,' 'data breach statement,' or 'holding statement.' Also covers crisis response when something goes wrong. For startup/SaaS/AI directory submissions, see directory-submissions. For product launches, see launch. For social-media engagement, see social. For cold-email outreach to prospects, see cold-email.

**S16. nestjs-expert**  
Creates and configures NestJS modules, controllers, services, DTOs, guards, and interceptors for enterprise-grade TypeScript backend applications. Use when building NestJS REST APIs or GraphQL services, implementing dependency injection, scaffolding modular architecture, adding JWT/Passport authentication, integrating TypeORM or Prisma, or working with .module.ts, .controller.ts, and .service.ts files. Invoke for guards, interceptors, pipes, validation, Swagger documentation, and unit/E2E testing in NestJS projects.

**S17. cold-email**  
Write and run B2B cold outbound that gets replies, from cold emails and follow-ups to sending setup, LinkedIn, multichannel cadences, and reply handling. Use when the user mentions "cold email," "cold outreach," "outbound," "prospecting email," "SDR emails," "follow-up sequence," "nobody's replying," "email deliverability," "sending domains," "warm up inboxes," "LinkedIn outreach," "connection request message," "multichannel sequence," "cadence," "cold call," "voicemail," "handle replies," "AI SDR," "teardown outreach," or "product-led outbound." Send from secondary domains, verify lists first, turn off open tracking, and stop every channel when a prospect replies. Copy avoids AI tells like 'it's not X, it's Y' reveals. For building the list and account research, see prospecting. For lifecycle email, see emails. For call scripts and sales collateral, see sales-enablement.

**S18. 19-ab-test-setup-global**  
Use when the user wants a VALID experiment instead of a guess — hypothesis, one variable, sample size and runtime math, statistical significance, primary versus secondary metrics, multi-arm designs, and a results template, across Optimizely, VWO, and native Meta and Google tests. Trigger on 'A/B test', 'split test', 'how long should I run the test', 'is this result significant', 'test two versions', 'which creative is actually better'. Also use when a winner was declared after two days on tiny numbers. Not for — scaling the proven winner, see `55-scaling-ads-global`; analyzing data already collected, see `13-data-analysis-global`; auditing the account, see `21-ads-audit-global`.

**S19. angular-architect**  
Generates Angular 17+ standalone components, configures advanced routing with lazy loading and guards, implements NgRx state management, applies RxJS patterns, and optimizes bundle performance. Use when building Angular 17+ applications with standalone components or signals, setting up NgRx stores, establishing RxJS reactive patterns, performance tuning, or writing Angular tests for enterprise apps.

**S20. sql-pro**  
Optimizes SQL queries, designs database schemas, and troubleshoots performance issues. Use when a user asks why their query is slow, needs help writing complex joins or aggregations, mentions database performance issues, or wants to design or migrate a schema. Invoke for complex queries, window functions, CTEs, indexing strategies, query plan analysis, covering index creation, recursive queries, EXPLAIN/ANALYZE interpretation, before/after query benchmarking, or migrating queries between database dialects (PostgreSQL, MySQL, SQL Server, Oracle).

**S21. content-strategy**  
When the user wants to plan a content strategy, decide what content to create, or figure out what topics to cover. Also use when the user mentions "content strategy," "what should I write about," "content ideas," "blog strategy," "topic clusters," "content planning," "editorial calendar," "content marketing," "content roadmap," "what content should I create," "blog topics," "content pillars," or "I don't know what to write." Use this whenever someone needs help deciding what content to produce, not just writing it. For writing individual pieces, see copywriting. For SEO-specific audits, see seo-audit. For social media content specifically, see social.

**S22. 32-seo-growth-global**  
Use when the user wants SEO growth, AI SEO, GEO/AEO, Google visibility, ChatGPT or Perplexity citations, schema, structured data, llms.txt, programmatic SEO, local SEO, comparison and alternative pages, directory submissions, Product Hunt, G2/Capterra, backlinks, or a site audit. Trigger on 'SEO audit', 'AI search visibility', 'AI citations', 'schema markup', 'pSEO', 'alternative page', 'submit to directories', or 'backlink plan'. Not for — first-time Google Business Profile setup, see `11-channel-setup-global`; writing the articles themselves, see `36-content-brief-global`.

**S23. programmatic-seo**  
When the user wants to create SEO-driven pages at scale using templates and data. Also use when the user mentions "programmatic SEO," "template pages," "pages at scale," "directory pages," "location pages," "[keyword] + [city] pages," "comparison pages," "integration pages," "building many pages for SEO," "pSEO," "generate 100 pages," "data-driven pages," "templated landing pages," or "which CMS for pSEO." Use this whenever someone wants to create many similar pages targeting different keywords or locations. For auditing existing SEO issues, see seo-audit. For content strategy planning, see content-strategy.

**S24. test-master**  
Generates test files, creates mocking strategies, analyzes code coverage, designs test architectures, and produces test plans and defect reports across functional, performance, and security testing disciplines. Use when writing unit tests, integration tests, or E2E tests; creating test strategies or automation frameworks; analyzing coverage gaps; performance testing with k6 or Artillery; security testing with OWASP methods; debugging flaky tests; or working on QA, regression, test automation, quality gates, shift-left testing, or test maintenance.

**S25. 37-social-caption-global**  
Use when the user needs ORGANIC social captions for Instagram, LinkedIn, TikTok, Facebook, or X — hook, mobile-readable body, two A/B variants, platform-correct hashtags, written to brand voice and funnel stage. Trigger on 'write a caption', 'Instagram caption', 'LinkedIn post copy', 'TikTok caption', 'caption for this photo', 'we have the image but no words'. Also use when the user drops a picture and asks what to write with it. Not for — paid ad copy with character limits and policy, see `05-ad-copy-global`; the spoken lines in a video, see `04-script-video-global`; the brief that precedes writing, see `36-content-brief-global`; long-form authority posts, see `26-thought-leadership-content-global`.

**S26. 57-next-ads-plan-global**  
Use when the next period ADS plan must be built from last period results — read the report and the audit, keep and scale winners, stop or fix losers, set new test hypotheses, split budget, plan three budget scenarios, and schedule the creative pipeline. Trigger on 'next month ads plan', 'plan ads from the report', 'what should we run next month', 'Q4 ads plan', 'next quarter media plan', 'the month ended, now what'. Also use when a report and an audit both exist and no decisions have been made. Not for — the first media plan before any data, see `54-media-plan-global`; scoring the account, see `21-ads-audit-global`; the next period CONTENT plan, see `40-next-content-plan-global`.

**S27. vue-expert-js**  
Creates Vue 3 components, builds vanilla JS composables, configures Vite projects, and sets up routing and state management using JavaScript only — no TypeScript. Generates JSDoc-typed code with @typedef, @param, and @returns annotations for full type coverage without a TS compiler. Use when building Vue 3 applications with JavaScript only (no TypeScript), when projects require JSDoc-based type hints, when migrating from Vue 2 Options API to Composition API in JS, or when teams prefer vanilla JavaScript, .mjs modules, or need quick prototypes without TypeScript setup.

**S28. competitor-profiling**  
When the user wants to research, profile, or analyze competitors from their URLs. Also use when the user mentions 'competitor profile,' 'competitor research,' 'competitor analysis,' 'profile this competitor,' 'analyze competitor,' 'competitive intelligence,' 'competitor deep dive,' 'who are my competitors,' 'competitor landscape,' 'competitor dossier,' 'competitive audit,' or 'research these competitors.' Input is a list of competitor URLs. Output is structured competitor profile markdown files. For creating comparison/alternative pages from profiles, see competitors. For sales-specific battle cards, see sales-enablement. For researching accounts you're selling to, see prospecting. For how a competitor grew over time and what to copy, see reverse-engineer-marketing.

**S29. site-architecture**  
When the user wants to plan, map, or restructure their website's page hierarchy, navigation, URL structure, or internal linking. Also use when the user mentions "sitemap," "site map," "visual sitemap," "site structure," "page hierarchy," "information architecture," "IA," "navigation design," "URL structure," "breadcrumbs," "internal linking strategy," "website planning," "what pages do I need," "how should I organize my site," "site navigation," "which website builder should I use," "migrate my website," or "add a blog to my existing site." Use this whenever someone is planning what pages a website should have, how they connect, and where to build them. NOT for XML sitemaps (that's technical SEO — see seo-audit). For SEO audits, see seo-audit. For structured data, see schema.

**S30. ai-seo**  
When the user wants to optimize content for AI search engines, get cited by LLMs, or appear in AI-generated answers. Also use when the user mentions 'AI SEO,' 'AEO,' 'GEO,' 'LLMO,' 'answer engine optimization,' 'generative engine optimization,' 'LLM optimization,' 'AI Overviews,' 'optimize for ChatGPT,' 'optimize for Perplexity,' 'AI citations,' 'AI visibility,' 'zero-click search,' 'how do I show up in AI answers,' 'LLM mentions,' 'optimize for Claude/Gemini,' 'llms.txt,' 'llms-full.txt,' 'OKF,' 'Open Knowledge Format,' 'knowledge bundle,' 'agent-readable site,' 'agent readiness,' 'is my site agent-ready,' 'WebMCP,' 'do listicles still work for AI,' 'ChatGPT stopped citing comparison pages,' 'how do LLMs see our brand,' 'LinkedIn for AEO,' or 'AI citation format shift.' Use this whenever someone wants their content to be cited or surfaced by AI assistants and AI search engines. For traditional technical and on-page SEO audits, see seo-audit. For structured data implementation, see schema.

**S31. debugging-wizard**  
Parses error messages, traces execution flow through stack traces, correlates log entries to identify failure points, and applies systematic hypothesis-driven methodology to isolate and resolve bugs. Use when investigating errors, analyzing stack traces, finding root causes of unexpected behavior, troubleshooting crashes, or performing log analysis, error investigation, or root cause analysis.

**S32. paywalls**  
When the user wants to create or optimize in-app paywalls, upgrade screens, upsell modals, or feature gates. Also use when the user mentions "paywall," "upgrade screen," "upgrade modal," "upsell," "feature gate," "convert free to paid," "freemium conversion," "trial expiration screen," "limit reached screen," "plan upgrade prompt," "in-app pricing," "free users won't upgrade," "trial to paid conversion," or "how do I get users to pay," "mobile app paywall," "subscription app paywall," or "App Store subscription pricing." Use this for any in-product moment where you're asking users to upgrade. Distinct from public pricing pages (see cro) — this focuses on in-product upgrade moments where the user has already experienced value. For pricing decisions, see pricing.

**S33. 42-image-brief-global**  
Use when ONE static image needs a full brief for a designer or photographer — visual objective, concept and mood, composition, overlay copy, colors and fonts read from the brand guideline, per-channel dimensions, deliverables, and approval criteria. Trigger on 'image brief', 'banner brief', 'thumbnail brief', 'brief for the designer', 'how should this graphic look', 'the design came back wrong again'. Also use when the user has a post idea and needs to explain the visual. Not for — a multi-slide sequence, see `43-carousel-brief-global`; tool-level production steps in Canva or Figma, see `45-design-tool-brief-global`; generating the image with AI, see `30-design-master-global`; an urgent same-day swap, see `48-quick-visual-brief-global`.

**S34. kotlin-specialist**  
Provides idiomatic Kotlin implementation patterns including coroutine concurrency, Flow stream handling, multiplatform architecture, Compose UI construction, Ktor server setup, and type-safe DSL design. Use when building Kotlin applications requiring coroutines, multiplatform development, or Android with Compose. Invoke for Flow API, KMP projects, Ktor servers, DSL design, sealed classes, suspend function, Android Kotlin, Kotlin Multiplatform.

**S35. dotnet-core-expert**  
Use when building .NET 8 applications with minimal APIs, clean architecture, or cloud-native microservices. Invoke for Entity Framework Core, CQRS with MediatR, JWT authentication, AOT compilation.

**S36. chaos-engineer**  
Designs chaos experiments, creates failure injection frameworks, and facilitates game day exercises for distributed systems — producing runbooks, experiment manifests, rollback procedures, and post-mortem templates. Use when designing chaos experiments, implementing failure injection frameworks, or conducting game day exercises. Invoke for chaos experiments, resilience testing, blast radius control, game days, antifragile systems, fault injection, Chaos Monkey, Litmus Chaos.

**S37. copy-editing**  
When the user wants to edit, review, or improve existing marketing copy, or refresh outdated content. Also use when the user mentions 'edit this copy,' 'review my copy,' 'copy feedback,' 'proofread,' 'polish this,' 'make this better,' 'copy sweep,' 'tighten this up,' 'this reads awkwardly,' 'clean up this text,' 'too wordy,' 'sharpen the messaging,' 'refresh this content,' 'update this page,' 'this content is outdated,' or 'content audit,' 'this sounds like AI,' 'AI slop,' 'de-slop this,' or 'make it sound human.' Use this when the user already has copy and wants it improved or refreshed rather than rewritten from scratch. Every edit removes AI tells such as 'it's not X, it's Y' reveals, 'no X, no Y, no Z' lists, and sentences that trail into extra comma clauses. A request to fix one line gets a point edit that changes only that line. For writing new copy, see copywriting.

**S38. fine-tuning-expert**  
Use when fine-tuning LLMs, training custom models, or adapting foundation models for specific tasks. Invoke for configuring LoRA/QLoRA adapters, preparing JSONL training datasets, setting hyperparameters for fine-tuning runs, adapter training, transfer learning, finetuning with Hugging Face PEFT, OpenAI fine-tuning, instruction tuning, RLHF, DPO, or quantizing and deploying fine-tuned models. Trigger terms include: LoRA, QLoRA, PEFT, finetuning, fine-tuning, adapter tuning, LLM training, model training, custom model.

**S39. 09-customer-insight-global**  
(no description)

**S40. social**  
When the user wants help creating, scheduling, or optimizing social media content for LinkedIn, Twitter/X, Instagram, TikTok, or Facebook, or wants to do social listening and engagement triage. Also use when the user mentions 'LinkedIn post,' 'Twitter thread,' 'social media,' 'content calendar,' 'social scheduling,' 'engagement,' 'viral content,' 'what should I post,' 'repurpose this content,' 'tweet ideas,' 'LinkedIn carousel,' 'social media strategy,' 'grow my following,' 'TikTok video,' 'Reels,' 'Shorts,' 'video script,' 'video hook,' 'short-form video,' 'create a reel,' 'social listening,' 'brand mentions,' 'competitor monitoring,' 'top posts to comment on,' 'find people asking for,' 'carousel,' 'slide-by-slide,' or 'document post.' Use this for social content, repurposing, scheduling, video scripts, and listening. Posts avoid AI tells like 'it's not X, it's Y' reveals and broetry. For broader content strategy, see content-strategy. For paid ads, see ad-creative. For earned media, see public-relations.

**S41. shopify-expert**  
Builds and debugs Shopify themes (.liquid files, theme.json, sections), develops custom Shopify apps (shopify.app.toml, OAuth, webhooks), and implements Storefront API integrations for headless storefronts. Use when building or customizing Shopify themes, creating Hydrogen or custom React storefronts, developing Shopify apps, implementing checkout UI extensions or Shopify Functions, optimizing performance, or integrating third-party services. Invoke for Liquid templating, Storefront API, app development, checkout customization, Shopify Plus features, App Bridge, Polaris, or Shopify CLI workflows.

**S42. reverse-engineer-marketing**  
When the user wants to reverse engineer how another company grew and copy what worked. Also use when the user mentions 'reverse engineer,' 'how did X grow,' 'how does X get customers,' 'growth teardown,' 'marketing teardown,' 'GTM teardown,' 'what is X doing for marketing,' 'what made X take off,' 'copy X's playbook,' 'funnel hack,' 'map their funnel,' 'what is everyone in my category doing,' 'where are competitors weak,' 'what is X hiring for,' or 'watch what X is doing.' Builds a dated log of a company's moves against its traffic curve from public data, sign-ups and demos, team interviews, job posts, reviews, and hidden signals (DNS, certificate logs, archives), compares it with the user's own data and with other competitors, finds where they're weak, and turns it all into plays routed to the skill that runs each one. For a snapshot profile of a competitor's positioning, pricing, and features, see competitor-profiling. For comparison pages, see competitors. For why a single post went viral, see social.

**S43. playwright-expert**  
Use when writing E2E tests with Playwright, setting up test infrastructure, or debugging flaky browser tests. Invoke to write test scripts, create page objects, configure test fixtures, set up reporters, add CI integration, implement API mocking, or perform visual regression testing. Trigger terms: Playwright, E2E test, end-to-end, browser testing, automation, UI testing, visual testing, Page Object Model, test flakiness.

**S44. launch**  
When the user wants to plan a product launch, feature announcement, or release strategy. Also use when the user mentions 'launch,' 'Product Hunt,' 'feature release,' 'announcement,' 'go-to-market,' 'beta launch,' 'early access,' 'waitlist,' 'product update,' 'how do I launch this,' 'site launch,' 'go live,' 'pre-launch QA,' 'launch checklist,' 'GTM plan,' 'we're about to ship,' 'changelog,' 'release notes,' or 'what did we ship this week.' Use this whenever someone is preparing to release something publicly. For ongoing marketing after launch, see marketing-ideas. For the offer being launched (bonuses, guarantees, scarcity, naming), see offers.

**S45. golang-pro**  
Implements concurrent Go patterns using goroutines and channels, designs and builds microservices with gRPC or REST, optimizes Go application performance with pprof, and enforces idiomatic Go with generics, interfaces, and robust error handling. Use when building Go applications requiring concurrent programming, microservices architecture, or high-performance systems. Invoke for goroutines, channels, Go generics, gRPC integration, CLI tools, benchmarks, or table-driven testing.

**S46. events**  
When the user wants to plan, run, sponsor, speak at, or get pipeline from events — webinars, conferences, trade shows, meetups, dinners, workshops, virtual summits, or user conferences. Also use when the user mentions 'event marketing,' 'field marketing,' 'run a webinar,' 'webinar funnel,' 'show-up rate,' 'should we sponsor,' 'sponsor a conference,' 'trade show booth,' 'booth strategy,' 'event ROI,' 'badge scans,' 'event follow-up,' 'speaking slot,' 'CFP,' 'conference talk,' 'host a dinner,' 'user conference,' or 'virtual summit.' Covers all four roles: hosting, sponsoring/exhibiting, speaking, and attending. For product launch moments, see launch. For the partnership side of joint webinars, see co-marketing. For ongoing community programs, see community-marketing. For podcast appearances, see public-relations. For the email sequences themselves, see emails.

**S47. 28-community-building-global**  
Use when a PERSONAL brand needs its OWN community — Skool, Mighty Networks, Discord, Circle, or Slack: a three-layer public, member, and inner-circle blueprint, seven-day onboarding, engagement rituals, a moderation playbook, and activation metrics. Trigger on 'build a community', 'Skool community', 'Mighty Networks setup', 'Discord for my audience', 'paid community', 'my group is dead and nobody posts'. Also use when the user has members and no reason for them to come back. Not for — posting inside communities you do NOT own, see `38-community-seeding-global`; pricing and packaging the membership, see `27-personal-brand-monetize-global`; referral loops, see `18-referral-program-global`.

**S48. code-reviewer**  
Analyzes code diffs and files to identify bugs, security vulnerabilities (SQL injection, XSS, insecure deserialization), code smells, N+1 queries, naming issues, and architectural concerns, then produces a structured review report with prioritized, actionable feedback. Use when reviewing pull requests, conducting code quality audits, identifying refactoring opportunities, or checking for security issues. Invoke for PR reviews, code quality checks, refactoring suggestions, review code, code quality. Complements specialized skills (security-reviewer, test-master) by providing broad-scope review across correctness, performance, maintainability, and test coverage in a single pass.

**S49. mcp-developer**  
Use when building, debugging, or extending MCP servers or clients that connect AI systems with external tools and data sources. Invoke to implement tool handlers, configure resource providers, set up stdio/HTTP/SSE transport layers, validate schemas with Zod or Pydantic, debug protocol compliance issues, or scaffold complete MCP server/client projects using TypeScript or Python SDKs.

**S50. rag-architect**  
Designs and implements production-grade RAG systems by chunking documents, generating embeddings, configuring vector stores, building hybrid search pipelines, applying reranking, and evaluating retrieval quality. Use when building RAG systems, vector databases, or knowledge-grounded AI applications requiring semantic search, document retrieval, context augmentation, similarity search, or embedding-based indexing.

**S51. ad-creative**  
When the user wants to generate, iterate, or scale ad creative — headlines, descriptions, primary text, or full ad variations — for any paid advertising platform. Also use when the user mentions 'ad copy variations,' 'ad creative,' 'generate headlines,' 'RSA headlines,' 'bulk ad copy,' 'creative testing,' 'write me some ads,' 'Facebook ad copy,' 'Google ad headlines,' 'LinkedIn ad text,' 'static ads,' 'ad templates,' 'iMessage ad,' 'ChatGPT ad,' 'creative strategy,' 'creative roadmap,' 'creative retro,' 'hook writing,' 'creative review page,' 'motion video ad,' 'faceless video ad,' 'UGC ad,' 'greenscreen ad,' 'TikTok/Reels ad format,' 'Meta ad format tier list,' or 'creative format taxonomy.' Use it to produce or iterate ad copy at scale. Copy avoids AI tells like 'it's not X, it's Y' reveals. For campaign strategy and targeting, see ads. For landing page copy, see copywriting. Test hypotheses must be falsifiable, naming the metric, the comparison, and the expected direction.

**S52. 47-design-review-global**  
Use when a finished DESIGN needs scoring and feedback — brand consistency, hierarchy plus readability plus accessibility, conversion elements, and platform fit, 10 points each, with per-issue feedback in issue, why, and how-to-fix form and a verdict of approve, minor fix, or redo. Trigger on 'design review', 'review this design', 'score this creative', 'is this design good', 'brand check on this banner', 'can we ship this or not'. Also use when the user just sends an image and asks for thoughts. Not for — reviewing copy and briefs, see `62-marketing-review-global`; writing the brief before production, see `42-image-brief-global`; the brand rules being checked against, see `46-brand-guideline-global`.

**S53. seo-audit**  
When the user wants to audit, review, or diagnose SEO issues on their site. Also use when the user mentions "SEO audit," "technical SEO," "why am I not ranking," "SEO issues," "on-page SEO," "meta tags review," "SEO health check," "my traffic dropped," "lost rankings," "not showing up in Google," "site isn't ranking," "Google update hit me," "page speed," "core web vitals," "crawl errors," "indexing issues," "stuck on page 2," "striking distance keywords," "title tag rewrite," "low CTR," "local SEO," "Google Business Profile," or "map pack." Use this even if the user just says something vague like "my SEO is bad" or "help with SEO" — start with an audit. For building pages at scale to target keywords, see programmatic-seo. For adding structured data, see schema. For AI search optimization, see ai-seo.

**S54. link-building**  
When the user wants to earn backlinks or third-party mentions that lift rankings and AI citations. Also use when the user mentions 'link building,' 'linkbuilding,' 'backlinks,' 'get more backlinks,' 'link prospecting,' 'link gap,' 'competitor backlinks,' 'unlinked mentions,' 'link reclamation,' 'broken link building,' 'resource pages,' 'guest posting,' 'get into best-of lists,' 'listicle outreach,' 'domain rating,' 'referring domains,' 'anchor text,' or 'is this link vendor safe.' Use this whenever rankings stall on authority or someone wants off-site mentions that AI engines cite. Never recommend buying links that pass ranking credit: paid or exchanged placements get rel=sponsored or nofollow. For press coverage and journalist requests, see public-relations. For directories, see directory-submissions. For reviews, see referrals. For Reddit and communities, see community-marketing. For on-site AI citation work, see ai-seo.

**S55. churn-prevention**  
When the user wants to reduce churn, build cancellation flows, set up save offers, recover failed payments, or implement retention strategies. Also use when the user mentions 'churn,' 'cancel flow,' 'offboarding,' 'save offer,' 'dunning,' 'failed payment recovery,' 'win-back,' 'retention,' 'exit survey,' 'pause subscription,' 'involuntary churn,' 'people keep canceling,' 'churn rate is too high,' 'how do I keep users,' or 'customers are leaving.' Use this whenever someone is losing subscribers or wants to build systems to prevent it. For post-cancel win-back email sequences, see emails. For in-app upgrade paywalls, see paywalls.

**S56. 04-script-video-global**  
Use when the user needs a spoken script for short-form video — TikTok, Reels, or YouTube Shorts — with a 3-part hook, second-by-second beats, two A/B variants, shoot notes, and a viral score. Switches between product mode and personal brand mode from the context file. Trigger on 'video script', 'TikTok script', 'Reels script', 'Shorts script', 'what should I say in the video', 'write me a hook for a video'. Also use when the user has a video idea and knows only the topic. Not for — cut and edit instructions for an editor, see `44-video-editor-brief-global`; paid ad copy, see `05-ad-copy-global`; briefing an outside creator to film it, see `06-ugc-egc-brief-global`.

**S57. marketing-plan**  
When the user needs a comprehensive marketing plan for a client, a company they advise, or their own product. Also use when the user mentions "marketing plan," "growth plan," "GTM plan," "go-to-market plan," "AARRR plan," "90-day marketing plan," "12-month marketing roadmap," "fractional CMO plan," "fCMO plan," "market sizing," "TAM SAM SOM," "how big is this market," or "market research." Generates an exhaustive 13-section plan structured by AARRR (Acquisition, Activation, Retention, Referral, Revenue), customized to the client's current budget, team, and stage, mapped to future funding milestones, cross-referenced with the 139-idea marketing-ideas library and an embedded 17-section current-state audit rubric, with a marketing operations stack showing which skills and MCP/API integrations execute each part. Outputs a Notion-paste-ready markdown document. For positioning and ICP context before planning, see product-marketing. For stage-specific deep work, see onboarding, signup, emails, referrals, pricing.

## Your labels

Fill in the role list for each skill, using the role names exactly as above:

```yaml
S01: []  # marketing-ideas
S02: []  # pricing
S03: []  # 14-email-marketing-global
S04: []  # 52-account-structure-global
S05: []  # 62-marketing-review-global
S06: []  # sales-enablement
S07: []  # 23-personal-brand-strategy-global
S08: []  # cro
S09: []  # rust-engineer
S10: []  # 67-agency-vendor-brief-global
S11: []  # image
S12: []  # directory-submissions
S13: []  # aso
S14: []  # offers
S15: []  # public-relations
S16: []  # nestjs-expert
S17: []  # cold-email
S18: []  # 19-ab-test-setup-global
S19: []  # angular-architect
S20: []  # sql-pro
S21: []  # content-strategy
S22: []  # 32-seo-growth-global
S23: []  # programmatic-seo
S24: []  # test-master
S25: []  # 37-social-caption-global
S26: []  # 57-next-ads-plan-global
S27: []  # vue-expert-js
S28: []  # competitor-profiling
S29: []  # site-architecture
S30: []  # ai-seo
S31: []  # debugging-wizard
S32: []  # paywalls
S33: []  # 42-image-brief-global
S34: []  # kotlin-specialist
S35: []  # dotnet-core-expert
S36: []  # chaos-engineer
S37: []  # copy-editing
S38: []  # fine-tuning-expert
S39: []  # 09-customer-insight-global
S40: []  # social
S41: []  # shopify-expert
S42: []  # reverse-engineer-marketing
S43: []  # playwright-expert
S44: []  # launch
S45: []  # golang-pro
S46: []  # events
S47: []  # 28-community-building-global
S48: []  # code-reviewer
S49: []  # mcp-developer
S50: []  # rag-architect
S51: []  # ad-creative
S52: []  # 47-design-review-global
S53: []  # seo-audit
S54: []  # link-building
S55: []  # churn-prevention
S56: []  # 04-script-video-global
S57: []  # marketing-plan
```
