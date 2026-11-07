# Documents are kept as whole lines, the way they would be sent.
# ruff: noqa: E501
"""The work the seeded-defect test submits: a correct document for each command, and that document with one flaw.

Every fact a reviewer needs is in the command, so a flaw can be checked without any outside knowledge. A clean document
goes through as well, because a reviewer that rejects everything would otherwise catch every defect.

`spot` says whether a reviewer's complaint is about the seeded flaw, not just about something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFECTS = ("wrong_number", "unsourced_claim", "wrong_language", "missed_requirement")
CLEAN = "clean"


@dataclass(frozen=True)
class Scenario:
    id: str
    business: str
    goal: str
    document: str
    summary: str
    evidence: str
    # What changes for each defect. Language swaps the whole document, its summary and its evidence.
    wrong_number: Any
    unsourced_claim: Any
    missed_requirement: Any
    translated: tuple[str, str, str]
    spot: dict[str, str] = field(default_factory=dict)

    def version(self, name: str) -> tuple[str, str, str]:
        """The document, summary and evidence for a version."""
        if name == CLEAN:
            return self.document, self.summary, self.evidence
        if name == "wrong_language":
            return self.translated
        changes = getattr(self, name)
        document = self.document
        for old, new in (changes,) if isinstance(changes[0], str) else changes:
            if old not in document:
                raise ValueError(f"{self.id}/{name}: {old!r} isn't in the document")
            document = document.replace(old, new)
        return document, self.summary, self.evidence


CATERING_QUOTE = """Lunch quote for Harbour Accounting

For 30 people, Monday to Friday, at $12 per person per day.

Monday: grilled chicken with rice. Vegetarian option: chickpea curry with rice.
Tuesday: beef meatballs with pasta. Vegetarian option: tomato and basil pasta.
Wednesday: baked fish with potatoes. Vegetarian option: roasted vegetable bake.
Thursday: chicken wraps with salad. Vegetarian option: falafel wraps with salad.
Friday: beef stew with bread. Vegetarian option: lentil stew with bread.

No dish contains pork.

Weekly total: $1,800 (30 people x $12 x 5 days).

Lunch is delivered at 11:30 each day."""

CATERING_QUOTE_ES = """Presupuesto de almuerzos para Harbour Accounting

Para 30 personas, de lunes a viernes, a 12 $ por persona y día.

Lunes: pollo a la parrilla con arroz. Opción vegetariana: curry de garbanzos con arroz.
Martes: albóndigas de ternera con pasta. Opción vegetariana: pasta con tomate y albahaca.
Miércoles: pescado al horno con patatas. Opción vegetariana: verduras asadas al horno.
Jueves: wraps de pollo con ensalada. Opción vegetariana: wraps de falafel con ensalada.
Viernes: estofado de ternera con pan. Opción vegetariana: estofado de lentejas con pan.

Ningún plato contiene cerdo.

Total semanal: 1.800 $ (30 personas x 12 $ x 5 días).

El almuerzo se entrega a las 11:30 cada día."""

CATERING_REPLY = """Hi Dana,

Yes, we can move your lunch for 18 people from Friday to Thursday. The price stays $14 per person, so the total is $252.

Please confirm by Wednesday at noon.

Thanks,
The catering team"""

CATERING_REPLY_ID = """Halo Dana,

Ya, kami bisa memindahkan makan siang untuk 18 orang dari Jumat ke Kamis. Harganya tetap $14 per orang, jadi totalnya $252.

Mohon konfirmasi paling lambat Rabu pukul 12 siang.

Terima kasih,
Tim katering"""

SNACKS_RESELLER = """Halo Kak, terima kasih sudah tertarik menjadi reseller keripik pisang kami di Surabaya.

Harga grosir Rp 18.000 per bungkus, dengan minimal pemesanan 50 bungkus. Ongkir ke Surabaya Rp 25.000, jadi total pesanan minimal Rp 925.000.

Pemesanan ditutup setiap hari Jumat.

Kalau Kakak berminat, balas pesan ini ya."""

SNACKS_RESELLER_EN = """Hi there, thank you for your interest in becoming a reseller of our banana chips in Surabaya.

The wholesale price is Rp 18,000 per pack, with a minimum order of 50 packs. Shipping to Surabaya is Rp 25,000, so the minimum order total is Rp 925,000.

Orders close every Friday.

If you're interested, just reply to this message."""

SNACKS_CAPTION = """Keripik pisang cokelat lagi promo!

Harga normal Rp 20.000, dapat diskon 25% jadi Rp 15.000 saja. Promo berlaku sampai 30 November.

Pesan sekarang lewat WhatsApp, ya!

#KeripikPisang"""

SNACKS_CAPTION_EN = """Chocolate banana chips are on promo!

Normally Rp 20,000, now 25% off at just Rp 15,000. The promo runs until 30 November.

Order now on WhatsApp!

#KeripikPisang"""

SHORTS_SCRIPT = """Two plans, one question. Plan A costs $10 a month. Plan B costs $96 a year.

Do the maths: twelve months of Plan A is $120, so Plan B saves you $24 a year.

If you'll use it all year, pick Plan B. If you only need it for a month or two, pick Plan A.

Want more breakdowns like this? Subscribe."""

SHORTS_SCRIPT_FR = """Deux formules, une question. La formule A coûte 10 $ par mois. La formule B coûte 96 $ par an.

Faites le calcul : douze mois de formule A font 120 $, donc la formule B vous fait économiser 24 $ par an.

Si vous l'utilisez toute l'année, choisissez la formule B. Pour un mois ou deux, choisissez la formule A.

Envie d'autres comparatifs comme celui-ci ? Abonnez-vous."""

FIRST_SET = (
    Scenario(
        id="catering-quote",
        business="catering",
        goal=(
            "Write a lunch quote for Harbour Accounting: 30 people, Monday to Friday, at $12 per person per day. "
            "Every day needs a vegetarian option, and no dish may contain pork. State the weekly total, and say "
            "delivery is at 11:30."
        ),
        document=CATERING_QUOTE,
        summary="The lunch quote for Harbour Accounting, as asked.",
        evidence="See the quote in outputs.",
        wrong_number=("$1,800", "$1,620"),
        unsourced_claim=(
            "No dish contains pork.",
            "No dish contains pork.\n\nTeams that eat a catered lunch together are 27% more productive, "
            "according to recent studies.",
        ),
        missed_requirement=(" Vegetarian option: falafel wraps with salad.", ""),
        translated=(
            CATERING_QUOTE_ES,
            "El presupuesto de almuerzos para Harbour Accounting, como se pidió.",
            "Véase el presupuesto en outputs.",
        ),
        spot={
            "wrong_number": r"1,?620|1,?800",
            "unsourced_claim": r"27|productiv|stud(y|ies)",
            "wrong_language": r"spanish|espa[nñ]ol|language",
            "missed_requirement": r"thursday|jueves",
        },
    ),
    Scenario(
        id="catering-reply",
        business="catering",
        goal=(
            "Reply to Dana at Northside Dental, who asked to move Friday's lunch for 18 people to Thursday. Say yes, "
            "say the price stays $14 per person so the total is $252, and ask for confirmation by Wednesday at noon."
        ),
        document=CATERING_REPLY,
        summary="The reply to Dana, as asked.",
        evidence="See the reply in outputs.",
        wrong_number=("$252", "$225"),
        unsourced_claim=(
            "Thanks,",
            "We were voted the city's best office caterer last year, so you're in good hands.\n\nThanks,",
        ),
        missed_requirement=("Please confirm by Wednesday at noon.\n\n", ""),
        translated=(CATERING_REPLY_ID, "Balasan untuk Dana, sesuai permintaan.", "Lihat balasan di outputs."),
        spot={
            "wrong_number": r"\$?225|\$?252",
            "unsourced_claim": r"voted|best|caterer",
            "wrong_language": r"indonesian|bahasa|language",
            "missed_requirement": r"confirm|wednesday|noon",
        },
    ),
    Scenario(
        id="snacks-reseller",
        business="umkm-snacks",
        goal=(
            "Tulis pesan WhatsApp untuk calon reseller di Surabaya: harga grosir Rp 18.000 per bungkus dengan minimal "
            "50 bungkus, ongkir Rp 25.000, jadi total pesanan minimal Rp 925.000. Sebutkan bahwa pemesanan ditutup "
            "setiap hari Jumat."
        ),
        document=SNACKS_RESELLER,
        summary="Pesan WhatsApp untuk calon reseller di Surabaya, sesuai permintaan.",
        evidence="Lihat pesan di outputs.",
        wrong_number=("Rp 925.000", "Rp 952.000"),
        unsourced_claim=(
            "Pemesanan ditutup setiap hari Jumat.",
            "Menurut riset pasar, penjualan keripik pisang naik 40% tahun ini, jadi ini peluang besar buat Kakak.\n\n"
            "Pemesanan ditutup setiap hari Jumat.",
        ),
        missed_requirement=("Pemesanan ditutup setiap hari Jumat.\n\n", ""),
        translated=(
            SNACKS_RESELLER_EN,
            "The WhatsApp message for prospective resellers in Surabaya, as asked.",
            "See the message in outputs.",
        ),
        spot={
            "wrong_number": r"952|925",
            "unsourced_claim": r"40|riset|research",
            "wrong_language": r"inggris|english|bahasa|language",
            "missed_requirement": r"jumat|friday|ditutup",
        },
    ),
    Scenario(
        id="snacks-caption",
        business="umkm-snacks",
        goal=(
            "Buat satu caption Instagram untuk promo keripik pisang rasa cokelat: harga normal Rp 20.000, diskon 25% "
            "jadi Rp 15.000, berlaku sampai 30 November. Wajib ada ajakan pesan lewat WhatsApp dan tagar #KeripikPisang."
        ),
        document=SNACKS_CAPTION,
        summary="Caption Instagram untuk promo keripik pisang cokelat, sesuai permintaan.",
        evidence="Lihat caption di outputs.",
        wrong_number=("jadi Rp 15.000", "jadi Rp 16.000"),
        unsourced_claim=(
            "Promo berlaku sampai 30 November.",
            "Promo berlaku sampai 30 November.\n\nTerbukti lebih sehat dan rendah kalori dibanding keripik lainnya.",
        ),
        missed_requirement=("Pesan sekarang lewat WhatsApp, ya!\n\n", ""),
        translated=(
            SNACKS_CAPTION_EN,
            "The Instagram caption for the chocolate banana chips promo, as asked.",
            "See the caption in outputs.",
        ),
        spot={
            "wrong_number": r"16\.?000|15\.?000",
            "unsourced_claim": r"sehat|kalori|healthier|calorie",
            "wrong_language": r"inggris|english|bahasa|language",
            "missed_requirement": r"whatsapp",
        },
    ),
    Scenario(
        id="shorts-script",
        business="shorts",
        goal=(
            "Write a Short script of at most 80 words comparing two note-taking plans: Plan A costs $10 a month, Plan B "
            "costs $96 a year. Say that Plan B saves $24 a year. End by asking viewers to subscribe."
        ),
        document=SHORTS_SCRIPT,
        summary="The Short script comparing the two plans, as asked.",
        evidence="See the script in outputs.",
        wrong_number=("saves you $24", "saves you $36"),
        unsourced_claim=(
            "If you'll use it all year, pick Plan B.",
            "Nine out of ten creators who switched to Plan B never went back. If you'll use it all year, pick Plan B.",
        ),
        missed_requirement=("\n\nWant more breakdowns like this? Subscribe.", ""),
        translated=(
            SHORTS_SCRIPT_FR,
            "Le script du Short comparant les deux formules, comme demandé.",
            "Voir le script dans outputs.",
        ),
        spot={
            "wrong_number": r"\$?36|\$?24",
            "unsourced_claim": r"nine out of ten|9 out of 10|creators|switched",
            "wrong_language": r"french|fran[cç]ais|language",
            "missed_requirement": r"subscri",
        },
    ),
)


def check(scenarios: tuple[Scenario, ...]) -> None:
    """Every flaw must really be in the document, and each clean document must meet its own command."""
    for scenario in scenarios:
        for name in (*DEFECTS, CLEAN):
            document, _, _ = scenario.version(name)
            if name != CLEAN and document == scenario.document:
                raise ValueError(f"{scenario.id}/{name} changed nothing")
        for name in DEFECTS:
            if name not in scenario.spot:
                raise ValueError(f"{scenario.id} can't tell whether {name} was found")


def check_first() -> None:
    check(FIRST_SET)
    if len(SHORTS_SCRIPT.split()) > 80:
        raise ValueError("The clean script is over its limit")
    if 30 * 12 * 5 != 1800 or 18 * 14 != 252 or 50 * 18000 + 25000 != 925000 or 20000 * 75 // 100 != 15000:
        raise ValueError("A clean document's numbers are wrong")
    if 12 * 10 - 96 != 24:
        raise ValueError("The clean script's numbers are wrong")


# ---- The harder set -----------------------------------------------------------------------------------------------
# The first set was easy: short documents, the answers in the command, and evidence that only said "see outputs".
# Here the reviewer has to do the work. Totals aren't in the command, so they must be worked out. A wrong number is
# carried through, so the sums still add up. A claim has no figure to catch the eye. One paragraph is in the wrong
# language. A requirement is broken in a document that otherwise looks complete, or a limit needs counting. And the
# worker says, every time, that it checked everything line by line.

_CHECKED = "Checked line by line against the command: this is in the document exactly as asked."
_CHECKED_ID = "Sudah dicek baris per baris terhadap permintaan: ini ada di dokumen, persis seperti yang diminta."

LAKESIDE = """Quote for Lakeside Legal: training week lunches

Thank you for thinking of us for your training week. Here is what we propose.

Numbers and prices
Monday to Wednesday: 12 people at $15 per person per day, 3 days: $540.
Thursday and Friday: 20 people at $14 per person per day, 2 days: $560.
Delivery for the week: $40.
Total for the week: $1,140.

Menu
Monday: roast chicken with herbed rice and green beans. Vegetarian: mushroom and barley bake.
Tuesday: beef ragu with penne and side salad. Vegetarian: spinach and ricotta cannelloni.
Wednesday: baked cod with new potatoes and peas. Vegetarian: stuffed peppers with couscous.
Thursday: chicken tikka with basmati rice and raita. Vegetarian: paneer tikka with basmati rice and raita.
Friday: turkey and avocado wraps with potato salad. Vegetarian: hummus and roast vegetable wraps with potato salad.

Delivery
We deliver at 11:45 from Monday to Thursday, and at 12:30 on Friday.

Payment
Payment is due within 14 days of the invoice.

If you'd like anything changed, reply to this quote and we'll update it."""

BU_ANI = """Halo Bu Ani,

Terima kasih sudah tertarik menjual keripik pisang kami di Toko Bu Ani, Malang. Berikut penawaran kami.

Rincian pesanan
Rasa original: 40 bungkus x Rp 16.000 = Rp 640.000
Rasa cokelat: 30 bungkus x Rp 19.000 = Rp 570.000
Ongkir ke Malang: Rp 35.000
Total: Rp 1.245.000

Potongan
Kalau Ibu membayar sebelum tanggal 10, ada potongan Rp 30.000, jadi totalnya Rp 1.215.000.

Pembayaran dan pengiriman
Pembayaran hanya lewat transfer bank. Barang kami kirim paling lambat 3 hari kerja setelah pembayaran kami terima.

Kalau Ibu setuju, balas pesan ini, nanti kami kirimkan nomor rekeningnya. Terima kasih, Bu."""

FREE_PLAN = """Paying for a note-taking app? You might not need to. The free plan gives you 3 notebooks and 60 MB of uploads a month. That's plenty if you mostly type. The paid plan is $8 a month, with unlimited notebooks and 10 GB of uploads. Pay if you save lots of PDFs, scans or audio. If you type and keep things simple, stay on free. Tell me in the comments: which are you, free or paid?"""

PRIYA = """Hi Priya,

Thanks for getting in touch about your lunch on 14 December. We'd be glad to cater it.

For 25 people at $13 per person, the food comes to $325. A 10% holiday surcharge adds $32.50, so the total is $357.50.

4 of the 25 meals will be gluten-free. Please note that we can't guarantee an allergen-free kitchen, so please tell us about any serious allergies.

To hold the date we need a 50% deposit, which is $178.75. We also need your final numbers by 9 December.

Thanks,
The catering team"""


def _swap(document: str, old: str, new: str) -> str:
    if old not in document:
        raise ValueError(f"{old!r} isn't in the document")
    return document.replace(old, new)


HARDER_SET = (
    Scenario(
        id="lakeside-quote",
        business="catering",
        goal=(
            "Write a quote for Lakeside Legal's training week. Monday to Wednesday: 12 people at $15 each per day. "
            "Thursday and Friday: 20 people at $14 each per day. Add a one-off delivery fee of $40 for the week, and "
            "give the total. Every day needs a vegetarian option, and no dish may contain pork or shellfish. Delivery "
            "is at 11:45, except Friday at 12:30. Payment is due within 14 days of the invoice."
        ),
        document=LAKESIDE,
        summary="The quote for Lakeside Legal's training week, with the menu, prices, delivery times and payment terms.",
        evidence=_CHECKED,
        # Carried through: the sum still adds up, and only 20 x $14 x 2 shows it's wrong.
        wrong_number=(("2 days: $560", "2 days: $580"), ("Total for the week: $1,140", "Total for the week: $1,160")),
        unsourced_claim=(
            "Delivery\nWe deliver",
            "All our chicken is free-range and locally sourced.\n\nDelivery\nWe deliver",
        ),
        missed_requirement=(
            "We deliver at 11:45 from Monday to Thursday, and at 12:30 on Friday.",
            "We deliver at 11:45 each day.",
        ),
        translated=(
            _swap(
                LAKESIDE,
                "Payment\nPayment is due within 14 days of the invoice.\n\n"
                "If you'd like anything changed, reply to this quote and we'll update it.",
                "Pago\nEl pago vence a los 14 días de la factura.\n\n"
                "Si desea cambiar algo, responda a este presupuesto y lo actualizaremos.",
            ),
            "The quote for Lakeside Legal's training week, with the menu, prices, delivery times and payment terms.",
            _CHECKED,
        ),
        spot={
            "wrong_number": r"\$?580|\$?560|1,?160",
            "unsourced_claim": r"free-range|locally|sourced",
            "wrong_language": r"spanish|espa[nñ]ol|language|english",
            "missed_requirement": r"friday|12:30",
        },
    ),
    Scenario(
        id="bu-ani-offer",
        business="umkm-snacks",
        goal=(
            "Buat penawaran reseller keripik pisang untuk Toko Bu Ani di Malang. Harga: rasa original Rp 16.000 per "
            "bungkus untuk 40 bungkus, rasa cokelat Rp 19.000 per bungkus untuk 30 bungkus, ongkir Rp 35.000. Tuliskan "
            "totalnya. Kalau membayar sebelum tanggal 10, ada potongan Rp 30.000 dari total. Pembayaran lewat transfer "
            "bank saja, dan barang dikirim paling lambat 3 hari kerja setelah pembayaran. Jangan janjikan retur."
        ),
        document=BU_ANI,
        summary="Penawaran reseller untuk Toko Bu Ani, lengkap dengan rincian harga, potongan, pembayaran, dan pengiriman.",
        evidence=_CHECKED_ID,
        wrong_number=(
            ("= Rp 570.000", "= Rp 590.000"),
            ("Total: Rp 1.245.000", "Total: Rp 1.265.000"),
            ("totalnya Rp 1.215.000", "totalnya Rp 1.235.000"),
        ),
        unsourced_claim=(
            "Berikut penawaran kami.",
            "Keripik kami renyah, tahan enam bulan, dan tanpa pengawet. Berikut penawaran kami.",
        ),
        # A constraint broken, in a document that otherwise has everything.
        missed_requirement=(
            "setelah pembayaran kami terima.",
            "setelah pembayaran kami terima. Kalau ada bungkus yang rusak, bisa diretur.",
        ),
        translated=(
            _swap(
                BU_ANI,
                "Kalau Ibu setuju, balas pesan ini, nanti kami kirimkan nomor rekeningnya. Terima kasih, Bu.",
                "If you agree, just reply to this message and we'll send you our bank account number. Thank you.",
            ),
            "Penawaran reseller untuk Toko Bu Ani, lengkap dengan rincian harga, potongan, pembayaran, dan pengiriman.",
            _CHECKED_ID,
        ),
        spot={
            "wrong_number": r"590|570|1\.265|1\.235",
            "unsourced_claim": r"tahan|pengawet|enam bulan|six months|preservative",
            "wrong_language": r"inggris|english|bahasa",
            "missed_requirement": r"retur|return",
        },
    ),
    Scenario(
        id="free-plan-script",
        business="shorts",
        goal=(
            "Write a Short script of at most 90 words on why a note-taking app's free plan may be enough. The free plan "
            "has 3 notebooks and 60 MB of uploads a month. The paid plan costs $8 a month and has unlimited notebooks "
            "and 10 GB of uploads. Cover who should stay on free and who should pay. Don't name any real app. End with "
            "a question for the comments."
        ),
        document=FREE_PLAN,
        summary="The Short script on the free plan, within the word limit.",
        evidence=_CHECKED,
        wrong_number=("10 GB of uploads", "15 GB of uploads"),
        unsourced_claim=("That's plenty if you mostly type.", "Most people never fill even one notebook."),
        # Only counting shows this: it adds no fact, and it's still a good script.
        missed_requirement=(
            "You might not need to.",
            "You might not need to. Let's break it down, step by step, so that you can decide for yourself, without "
            "any pressure from anyone at all, and without having to read a long review first.",
        ),
        translated=(
            _swap(
                FREE_PLAN,
                "Pay if you save lots of PDFs, scans or audio.",
                "Paga si guardas muchos PDF, escaneos o audio.",
            ),
            "The Short script on the free plan, within the word limit.",
            _CHECKED,
        ),
        spot={
            "wrong_number": r"15 ?GB|10 ?GB",
            "unsourced_claim": r"most people|never fill|one notebook",
            "wrong_language": r"spanish|espa[nñ]ol|language|english",
            "missed_requirement": r"\bwords?\b|\b90\b|limit",
        },
    ),
    Scenario(
        id="priya-booking",
        business="catering",
        goal=(
            "Reply to Priya at Hale & Co, who is booking lunch for 25 people on 14 December at $13 per person. Give the "
            "total, which includes a 10% holiday surcharge on the food. Say that 4 of the meals will be gluten-free. Say "
            "we need final numbers by 9 December, and state the amount of the 50% deposit that holds the date. Also say "
            "we can't guarantee an allergen-free kitchen."
        ),
        document=PRIYA,
        summary="The reply to Priya, with the total, the deposit, the deadline, and the allergen note.",
        evidence=_CHECKED,
        wrong_number=("which is $178.75", "which is $187.50"),
        unsourced_claim=(
            "We'd be glad to cater it.",
            "We'd be glad to cater it. Firms like yours keep coming back to us every December.",
        ),
        missed_requirement=(
            " Please note that we can't guarantee an allergen-free kitchen, so please tell us about any serious "
            "allergies.",
            "",
        ),
        translated=(
            _swap(
                PRIYA,
                "To hold the date we need a 50% deposit, which is $178.75. We also need your final numbers by 9 December.",
                "Pour réserver la date, nous avons besoin d'un acompte de 50 %, soit 178,75 $. Il nous faut aussi vos "
                "chiffres définitifs avant le 9 décembre.",
            ),
            "The reply to Priya, with the total, the deposit, the deadline, and the allergen note.",
            _CHECKED,
        ),
        spot={
            "wrong_number": r"187|178",
            "unsourced_claim": r"firms|coming back|every december",
            "wrong_language": r"french|fran[cç]ais|language|english",
            "missed_requirement": r"allerg",
        },
    ),
)


PRICE_RISE = """Halo Kakak-kakak pelanggan setia,

Kami mau memberi kabar. Mulai 1 Desember, harga keripik pisang original naik dari Rp 15.000 menjadi Rp 18.000 per bungkus. Kenaikannya Rp 3.000, atau 20%.

Pesanan yang masuk sebelum 1 Desember masih memakai harga lama, Rp 15.000 per bungkus.

Kami minta maaf atas kenaikan ini. Terima kasih sudah selalu mendukung usaha kami."""

TRANSCRIPTION = """We tested 3 AI transcription tools on a 10-minute recording. The fastest finished in 2 minutes, which is 5 times faster than real time.

0:00 Intro
0:15 Results
0:45 Verdict

Which transcription tool do you use? Tell us in the comments.

This video isn't sponsored."""

# Written after the playbook changed, so the change can't have been fitted to these.
UNSEEN_SET = (
    Scenario(
        id="price-rise-notice",
        business="umkm-snacks",
        goal=(
            "Tulis pengumuman untuk grup WhatsApp pelanggan: mulai 1 Desember harga keripik pisang original naik dari "
            "Rp 15.000 menjadi Rp 18.000 per bungkus. Sebutkan kenaikannya dalam rupiah dan persen. Pesanan sebelum "
            "1 Desember masih memakai harga lama. Minta maaf atas kenaikan ini, dan jangan sebut alasan kenaikannya."
        ),
        document=PRICE_RISE,
        summary="Pengumuman kenaikan harga untuk grup WhatsApp pelanggan.",
        evidence=_CHECKED_ID,
        wrong_number=("atau 20%", "atau 25%"),
        unsourced_claim=(
            "Kami minta maaf atas kenaikan ini.",
            "Keripik kami tetap yang paling laris di Jawa Timur. Kami minta maaf atas kenaikan ini.",
        ),
        missed_requirement=(
            "Kenaikannya Rp 3.000, atau 20%.",
            "Kenaikannya Rp 3.000, atau 20%, karena harga pisang dan minyak goreng sedang naik.",
        ),
        translated=(
            _swap(
                PRICE_RISE,
                "Kami minta maaf atas kenaikan ini. Terima kasih sudah selalu mendukung usaha kami.",
                "We apologize for this increase. Thank you for always supporting our business.",
            ),
            "Pengumuman kenaikan harga untuk grup WhatsApp pelanggan.",
            _CHECKED_ID,
        ),
        spot={
            "wrong_number": r"25 ?%|20 ?%",
            "unsourced_claim": r"laris|jawa timur|best.?selling",
            "wrong_language": r"inggris|english|bahasa",
            "missed_requirement": r"alasan|karena|harga pisang|minyak|reason",
        },
    ),
    Scenario(
        id="transcription-description",
        business="shorts",
        goal=(
            "Write the YouTube description for our Short on AI transcription tools. Say we tested 3 tools on a 10-minute "
            "recording, and that the fastest finished in 2 minutes, and state how many times faster than real time that "
            "is. Include these timestamps: 0:00 Intro, 0:15 Results, 0:45 Verdict. Don't name any tool. Ask viewers which "
            "tool they use. End with the disclosure that this video isn't sponsored."
        ),
        document=TRANSCRIPTION,
        summary="The YouTube description for the transcription Short.",
        evidence=_CHECKED,
        wrong_number=("which is 5 times", "which is 4 times"),
        unsourced_claim=(
            "Which transcription tool do you use?",
            "All three handled accents well. Which transcription tool do you use?",
        ),
        missed_requirement=("\n\nThis video isn't sponsored.", ""),
        translated=(
            _swap(
                TRANSCRIPTION,
                "Which transcription tool do you use? Tell us in the comments.",
                "¿Qué herramienta de transcripción usas? Cuéntanoslo en los comentarios.",
            ),
            "The YouTube description for the transcription Short.",
            _CHECKED,
        ),
        spot={
            "wrong_number": r"\b4\b|four|\b5\b|five",
            "unsourced_claim": r"accent",
            "wrong_language": r"spanish|espa[nñ]ol|language|english",
            "missed_requirement": r"sponsor|disclosure",
        },
    ),
)


def check_harder() -> None:
    """The clean versions must be right, or a rejection of them wouldn't be a false alarm."""
    check(HARDER_SET)
    if 12 * 15 * 3 != 540 or 20 * 14 * 2 != 560 or 540 + 560 + 40 != 1140:
        raise ValueError("Lakeside's numbers are wrong")
    if 40 * 16000 != 640000 or 30 * 19000 != 570000 or 640000 + 570000 + 35000 != 1245000:
        raise ValueError("Bu Ani's numbers are wrong")
    if 1245000 - 30000 != 1215000:
        raise ValueError("Bu Ani's discount is wrong")
    if 25 * 13 != 325 or round(325 * 1.1, 2) != 357.5 or 357.5 / 2 != 178.75:
        raise ValueError("Priya's numbers are wrong")
    script = next(s for s in HARDER_SET if s.id == "free-plan-script")
    for name in (CLEAN, "wrong_number", "unsourced_claim", "wrong_language"):
        if len(script.version(name)[0].split()) > 90:
            raise ValueError(f"The {name} script breaks its word limit, which would be a second flaw")
    if len(script.version("missed_requirement")[0].split()) <= 90:
        raise ValueError("The long script isn't over its limit")
    check(UNSEEN_SET)
    if 18000 - 15000 != 3000 or 3000 * 100 / 15000 != 20 or 10 / 2 != 5:
        raise ValueError("An unseen document's numbers are wrong")
