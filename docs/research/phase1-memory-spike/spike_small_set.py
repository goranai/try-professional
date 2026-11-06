"""Throwaway Phase 1 feasibility spike. Not project code.

Answers three questions against the real mnemosyne-memory package:
  1. Do named banks isolate data when opened with an explicit data dir?
  2. Does the configured multilingual model actually get used?
  3. Does an English skill description come back for queries in 5 languages,
     (a) through Mnemosyne's hybrid recall and (b) through raw cosine similarity?
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
data_dir = Path(tempfile.mkdtemp(prefix="mnemo-spike-"))
os.environ["MNEMOSYNE_DATA_DIR"] = str(data_dir)
os.environ["MNEMOSYNE_EMBEDDING_MODEL"] = MODEL
os.environ["MNEMOSYNE_EMBEDDING_DIM"] = "384"
os.environ["HOME"] = str(data_dir)
os.environ["USERPROFILE"] = str(data_dir)

import numpy as np  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402
from mnemosyne.core.banks import BankManager  # noqa: E402
from mnemosyne.core.memory import Mnemosyne  # noqa: E402

SKILLS = {
    "seo-audit": "Audit a website for search engine optimization problems and suggest keyword improvements.",
    "code-review": "Review a pull request for bugs, security issues, and code quality problems.",
    "invoice-gen": "Generate a customer invoice with line items, tax, and payment terms.",
    "contract-check": "Check a contract for risky clauses and summarize legal obligations.",
    "docker-deploy": "Build a Docker image and deploy the service to production with a rollback plan.",
    "ux-interview": "Plan and summarize user research interviews to find usability problems.",
    "social-calendar": "Create a monthly social media content calendar with post ideas.",
    "budget-forecast": "Forecast quarterly budget and track spending against the plan.",
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

report = {"python": sys.version.split()[0], "platform": sys.platform, "model": MODEL}

# 1. Bank isolation
bm = BankManager(data_dir=data_dir)
for name in ("dept-engineering", "dept-marketing", "company", "skills"):
    bm.create_bank(name)
eng = Mnemosyne(session_id="spike", bank="dept-engineering")
mkt = Mnemosyne(session_id="spike", bank="dept-marketing")
eng.remember("The staging database password rotation runbook lives in the ops wiki.", scope="global", importance=0.9)
hits_eng = eng.recall("staging database runbook", top_k=3)
hits_mkt = mkt.recall("staging database runbook", top_k=3)
report["bank_isolation"] = {
    "banks": sorted(bm.list_banks()),
    "hits_in_owner_bank": len(hits_eng),
    "hits_in_other_bank": len(hits_mkt),
    "db_paths_differ": str(eng.db_path) != str(mkt.db_path),
}

# 2/3. Skill retrieval
t0 = time.time()
skills = Mnemosyne(session_id="spike", bank="skills")
for sid, desc in SKILLS.items():
    skills.remember(f"{sid}: {desc}", source="skill-index", scope="global", importance=0.5, metadata={"skill_id": sid})
report["index_seconds"] = round(time.time() - t0, 1)

embedder = TextEmbedding(model_name=MODEL)
ids = list(SKILLS)
doc_vecs = np.array(list(embedder.embed([SKILLS[i] for i in ids])))
doc_vecs /= np.linalg.norm(doc_vecs, axis=1, keepdims=True)
report["embedding_dim"] = int(doc_vecs.shape[1])

results = {"hybrid": {}, "cosine": {}}
misses = []
for mode in results:
    for lang in ("en", "id", "es", "zh", "ar"):
        results[mode][lang] = {"top1": 0, "top3": 0, "n": 0}
for target, by_lang in QUERIES.items():
    for lang, q in by_lang.items():
        hits = skills.recall(q, top_k=3)
        ranked = []
        for h in hits:
            text = h.get("content", "")
            sid = text.split(":", 1)[0]
            if sid in SKILLS and sid not in ranked:
                ranked.append(sid)
        r = results["hybrid"][lang]
        r["n"] += 1
        r["top1"] += int(ranked[:1] == [target])
        r["top3"] += int(target in ranked[:3])
        if target not in ranked[:1]:
            misses.append({"mode": "hybrid", "lang": lang, "want": target, "got": ranked[:3]})

        v = np.array(list(embedder.embed([q])))[0]
        v /= np.linalg.norm(v)
        order = [ids[i] for i in np.argsort(-(doc_vecs @ v))]
        r = results["cosine"][lang]
        r["n"] += 1
        r["top1"] += int(order[0] == target)
        r["top3"] += int(target in order[:3])
        if order[0] != target:
            misses.append({"mode": "cosine", "lang": lang, "want": target, "got": order[:3]})

report["retrieval"] = results
report["misses"] = misses
report["sample_hit_keys"] = sorted(hits[0].keys()) if hits else []
report["data_files"] = sorted(str(p.relative_to(data_dir)) for p in data_dir.rglob("*.db"))
out = Path(sys.argv[1])
out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out)
