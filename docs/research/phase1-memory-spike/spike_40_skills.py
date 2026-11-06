"""Throwaway Phase 1 spike #2. Not project code.

usage: spike_memory2.py <out.json> [ENV=VALUE ...]
Harder set: 8 labelled targets + 32 distractors (several deliberately close).
Each config runs in its own process because Mnemosyne resolves some env at import.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    os.environ[k] = v
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
data_dir = Path(tempfile.mkdtemp(prefix="mnemo-spike-"))
os.environ["MNEMOSYNE_DATA_DIR"] = str(data_dir)
os.environ["MNEMOSYNE_EMBEDDING_MODEL"] = MODEL
os.environ["MNEMOSYNE_EMBEDDING_DIM"] = "384"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
cache = str(Path(sys.argv[1]).parent / "fastembed-cache")
os.environ["MNEMOSYNE_FASTEMBED_CACHE_DIR"] = cache

import numpy as np  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402
from mnemosyne.core.banks import BankManager  # noqa: E402
from mnemosyne.core.memory import Mnemosyne  # noqa: E402

TARGETS = {
    "seo-audit": "Audit a website for search engine optimization problems and suggest keyword improvements.",
    "code-review": "Review a pull request for bugs, security issues, and code quality problems.",
    "invoice-gen": "Generate a customer invoice with line items, tax, and payment terms.",
    "contract-check": "Check a contract for risky clauses and summarize legal obligations.",
    "docker-deploy": "Build a Docker image and deploy the service to production with a rollback plan.",
    "ux-interview": "Plan and summarize user research interviews to find usability problems.",
    "social-calendar": "Create a monthly social media content calendar with post ideas.",
    "budget-forecast": "Forecast quarterly budget and track spending against the plan.",
}
DISTRACTORS = {
    "keyword-research": "Find high-volume search keywords for a new product category.",
    "backlink-outreach": "Draft outreach emails to earn backlinks from relevant blogs.",
    "meta-tags": "Write page titles and meta descriptions for marketing pages.",
    "unit-test-gen": "Write unit tests for an existing Python module.",
    "refactor-plan": "Plan a safe refactor of a large legacy codebase.",
    "security-scan": "Scan application dependencies for known security vulnerabilities.",
    "commit-message": "Write clear git commit messages from a diff.",
    "payment-reminder": "Send polite reminders to customers about overdue payments.",
    "expense-report": "Summarize employee expense reports and flag policy violations.",
    "tax-summary": "Prepare a summary of annual tax obligations for a small company.",
    "nda-draft": "Draft a mutual non-disclosure agreement between two companies.",
    "privacy-policy": "Write a website privacy policy that covers personal data handling.",
    "license-audit": "Check open source dependency licenses for commercial compatibility.",
    "k8s-manifest": "Write Kubernetes manifests for a web service with autoscaling.",
    "ci-pipeline": "Set up a continuous integration pipeline that runs tests on each commit.",
    "incident-postmortem": "Write a blameless postmortem after a production outage.",
    "survey-design": "Design a customer satisfaction survey with unbiased questions.",
    "persona-builder": "Build user personas from customer data and interviews.",
    "wireframe-review": "Review interface wireframes for accessibility issues.",
    "tweet-thread": "Turn a blog post into a thread of short social media posts.",
    "newsletter": "Write a weekly email newsletter for subscribers.",
    "press-release": "Draft a press release for a product launch.",
    "cash-flow": "Project monthly cash flow and warn about shortfalls.",
    "pricing-model": "Compare pricing models for a subscription product.",
    "sales-proposal": "Write a sales proposal tailored to a prospective client.",
    "lead-qualify": "Score inbound sales leads and recommend who to contact first.",
    "market-sizing": "Estimate total addressable market for a new product.",
    "competitor-brief": "Summarize competitor products, pricing, and positioning.",
    "literature-review": "Summarize academic papers on a research question.",
    "support-macro": "Write canned replies for common customer support tickets.",
    "onboarding-doc": "Write an onboarding guide for new employees.",
    "meeting-notes": "Turn a meeting transcript into decisions and action items.",
}
QUERIES = {
    "seo-audit": {"en": "improve my website ranking on Google", "id": "tingkatkan peringkat situs web saya di mesin pencari", "es": "mejorar el posicionamiento de mi sitio web en buscadores", "zh": "提升我的网站在搜索引擎中的排名", "ar": "تحسين ترتيب موقعي في محركات البحث"},
    "code-review": {"en": "check my pull request for bugs", "id": "periksa pull request saya untuk menemukan bug", "es": "revisar mi código en busca de errores", "zh": "审查我的代码有没有错误", "ar": "مراجعة الكود الخاص بي للعثور على الأخطاء"},
    "invoice-gen": {"en": "bill a client for my work", "id": "buat faktur untuk pelanggan", "es": "crear una factura para un cliente", "zh": "给客户开发票", "ar": "إنشاء فاتورة للعميل"},
    "contract-check": {"en": "find dangerous terms in this agreement", "id": "periksa klausul berisiko dalam kontrak", "es": "revisar cláusulas de riesgo en un contrato", "zh": "检查合同中的风险条款", "ar": "فحص البنود الخطرة في العقد"},
    "docker-deploy": {"en": "ship my container to production", "id": "terapkan layanan ke produksi dengan Docker", "es": "desplegar el servicio en producción con Docker", "zh": "用 Docker 把服务部署到生产环境", "ar": "نشر الخدمة في بيئة الإنتاج باستخدام Docker"},
    "ux-interview": {"en": "talk to users to learn what confuses them", "id": "wawancara pengguna untuk riset kegunaan", "es": "entrevistar a usuarios para investigar la usabilidad", "zh": "采访用户以研究可用性问题", "ar": "مقابلة المستخدمين لدراسة سهولة الاستخدام"},
    "social-calendar": {"en": "plan next month's Instagram posts", "id": "buat jadwal konten media sosial bulanan", "es": "planificar publicaciones en redes sociales del mes", "zh": "制定每月社交媒体内容日历", "ar": "إعداد جدول شهري لمحتوى وسائل التواصل الاجتماعي"},
    "budget-forecast": {"en": "estimate spending for next quarter", "id": "perkiraan anggaran kuartal berikutnya", "es": "prever el presupuesto del próximo trimestre", "zh": "预测下季度的预算", "ar": "توقع ميزانية الربع القادم"},
}
NONSENSE = ["purple bicycle quantum oatmeal", "sepeda ungu kuantum bubur", "紫色自行车量子燕麦片", "دراجة أرجوانية كمومية شوفان"]
LANGS = ("en", "id", "es", "zh", "ar")
ALL = {**TARGETS, **DISTRACTORS}


def parse(hits):
    ranked = []
    for h in hits:
        sid = h.get("content", "").split(":", 1)[0]
        if sid in ALL and sid not in ranked:
            ranked.append(sid)
    return ranked


BankManager(data_dir=data_dir).create_bank("skills")
skills = Mnemosyne(session_id="spike", bank="skills")
t0 = time.time()
for sid, desc in ALL.items():
    skills.remember(f"{sid}: {desc}", source="skill-index", scope="global", importance=0.5)
index_s = time.time() - t0

score = {m: {l: {"r1": 0, "r5": 0, "n": 0} for l in LANGS} for m in ("hybrid", "cosine")}
embedder = TextEmbedding(model_name=MODEL, cache_dir=cache)
ids = list(ALL)
docs = np.array(list(embedder.embed([f"{i}: {ALL[i]}" for i in ids])))
docs /= np.linalg.norm(docs, axis=1, keepdims=True)
misses = []
t0 = time.time()
n_recall = 0
for target, by_lang in QUERIES.items():
    for lang, q in by_lang.items():
        ranked = parse(skills.recall(q, top_k=5))
        n_recall += 1
        s = score["hybrid"][lang]
        s["n"] += 1
        s["r1"] += int(ranked[:1] == [target])
        s["r5"] += int(target in ranked[:5])
        v = np.array(list(embedder.embed([q])))[0]
        v /= np.linalg.norm(v)
        order = [ids[i] for i in np.argsort(-(docs @ v))]
        s = score["cosine"][lang]
        s["n"] += 1
        s["r1"] += int(order[0] == target)
        s["r5"] += int(target in order[:5])
        if target not in order[:5]:
            misses.append({"mode": "cosine", "lang": lang, "want": target, "got": order[:5]})
        if target not in ranked[:5]:
            misses.append({"mode": "hybrid", "lang": lang, "want": target, "got": ranked[:5]})
recall_ms = 1000 * (time.time() - t0) / n_recall

nonsense_hits = {q: len(parse(skills.recall(q, top_k=5))) for q in NONSENSE}
total = {m: {"r1": sum(score[m][l]["r1"] for l in LANGS), "r5": sum(score[m][l]["r5"] for l in LANGS), "n": sum(score[m][l]["n"] for l in LANGS)} for m in score}
hits = skills.recall("Docker production deploy", top_k=1)
report = {
    "env": sys.argv[2:],
    "skills_indexed": len(ALL),
    "index_seconds": round(index_s, 2),
    "total": total,
    "per_language": score,
    "nonsense_hits": nonsense_hits,
    "cosine_misses": [m for m in misses if m["mode"] == "cosine"],
    "hybrid_miss_count": len([m for m in misses if m["mode"] == "hybrid"]),
    "sample_hit": {k: (str(v)[:80]) for k, v in hits[0].items()} if hits else {},
    "avg_recall_ms_hybrid_plus_cosine": round(recall_ms, 1),
}
Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"env": sys.argv[2:], "total": total, "nonsense": list(nonsense_hits.values())}))
