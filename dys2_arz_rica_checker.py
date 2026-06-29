from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class DecisionResult:
    relation: str
    expected_closing: str
    found_closing: Optional[str]
    is_current_closing_ok: bool
    suggested_fix: Optional[str]
    forbidden_phrases_found: list[str]
    wrong_word_suggestions: list[dict[str, str]]
    debug: dict[str, Any]


_CLOSING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(arz\s+ve\s+rica\s+ederim)\b\.?"),
    re.compile(r"(?i)\b(arz\s*/\s*rica\s+ederim)\b\.?"),
    re.compile(r"(?i)\b(arz\s+ederim)\b\.?"),
    re.compile(r"(?i)\b(rica\s+ederim)\b\.?"),
]


def _load_rules(rules_path: Path) -> dict[str, Any]:
    return json.loads(rules_path.read_text(encoding="utf-8"))


def _load_word_maps(word_maps_path: Path) -> dict[str, Any]:
    return json.loads(word_maps_path.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    # Türkçe 'İ' gibi karakterlerde lower() sonucu oluşan birleşik nokta işaretini temizlemek için:
    # - casefold
    # - NFKD normalize
    # - combining mark'ları kaldır
    # Ardından whitespace normalize
    cf = s.strip().casefold()
    nfkd = unicodedata.normalize("NFKD", cf)
    no_marks = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_marks)


def _detect_forbidden_phrases(text: str, rules: dict[str, Any]) -> list[str]:
    phrases = rules.get("forbidden_phrases", {}).get("phrases", [])
    t = _norm(text)
    found: list[str] = []
    for p in phrases:
        pn = _norm(str(p))
        if pn and pn in t:
            found.append(str(p))
    return found


def _detect_wrong_words(text: str, word_maps: dict[str, Any]) -> list[dict[str, str]]:
    mappings = (
        word_maps.get("wrong_to_correct", {}).get("mappings")
        or word_maps.get("wrong_to_correct", {}).get("mappings", {})
    )
    if not isinstance(mappings, dict) or not mappings:
        return []

    t = _norm(text)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for wrong, correct in mappings.items():
        w = _norm(str(wrong))
        c = str(correct)
        if not w:
            continue

        # Çok kelimeli ifadeler için "contains"; tek kelime için ek/çekim almış haller dahil.
        if " " in w:
            hit = w in t
        else:
            # Not: metin normalleştirilmiş olduğu için burada t üzerinde arıyoruz.
            # \w unicode'da harf/sayı/_ kapsar; ek/çekim almış biçimleri yakalamak için \w* ekliyoruz.
            hit = bool(re.search(rf"\\b{re.escape(w)}\\w*\\b", t)) or (w in t)

        if hit and w not in seen:
            seen.add(w)
            out.append({"wrong": str(wrong), "correct": c})

    return out


def _apply_word_corrections(text: str, word_maps: dict[str, Any]) -> str:
    """Metinde bulunan yanlış kelimeleri word_maps'e göre düzeltir."""
    mappings = (
        word_maps.get("wrong_to_correct", {}).get("mappings")
        or word_maps.get("wrong_to_correct", {}).get("mappings", {})
    )
    if not isinstance(mappings, dict) or not mappings:
        return text

    result = text
    # Önce uzun kelimeleri değiştir (çok kelimeli ifadeler önce)
    sorted_mappings = sorted(mappings.items(), key=lambda x: len(str(x[0])), reverse=True)
    
    for wrong, correct in sorted_mappings:
        wrong_str = str(wrong)
        correct_str = str(correct)
        
        # Çok kelimeli ifadeler için basit replace
        if " " in wrong_str:
            # Case-insensitive replace
            pattern = re.compile(re.escape(wrong_str), re.IGNORECASE)
            result = pattern.sub(correct_str, result)
        else:
            # Tek kelime için kelime sınırlarını kullan
            # Tam kelime eşleşmesi (ek/çekim almamış)
            pattern = re.compile(r"\b" + re.escape(wrong_str) + r"\b", re.IGNORECASE)
            result = pattern.sub(correct_str, result)
    
    return result


def _find_closing_phrase(text: str) -> Optional[str]:
    for pat in _CLOSING_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _is_non_public_entity(name: str, rules: dict[str, Any]) -> bool:
    kws = rules.get("external_entity_keywords", {}).get("non_public_entity_keywords", [])
    n = _norm(name)
    return any(_norm(str(kw)) in n for kw in kws)


def _infer_level_from_title(title: Optional[str], rules: dict[str, Any]) -> Optional[int]:
    if not title:
        return None
    levels: dict[str, Any] = rules.get("hierarchy_levels", {}).get("levels", {})
    t = _norm(title)

    # En iyi eşleşme: metin içinde geçen en uzun anahtar kelimeyi seç
    best: tuple[int, int] | None = None  # (level, keyword_len)
    for k, v in levels.items():
        kn = _norm(str(k))
        if not kn:
            continue
        if kn in t:
            try:
                lvl = int(v)
            except Exception:
                continue
            cand = (lvl, len(kn))
            if best is None:
                best = cand
            else:
                # Daha spesifik anahtar kelimeyi tercih et
                if cand[1] > best[1]:
                    best = cand
                elif cand[1] == best[1] and cand[0] < best[0]:
                    best = cand

    return best[0] if best else None


def _relation_from_levels(sender_level: Optional[int], recipient_levels: list[Optional[int]]) -> str:
    # unknown
    if sender_level is None:
        return "unknown"

    known = [lvl for lvl in recipient_levels if lvl is not None]
    if not known:
        return "unknown"

    # lower sayı = daha üst
    any_recipient_higher = any(lvl < sender_level for lvl in known)
    any_recipient_lower = any(lvl > sender_level for lvl in known)
    any_recipient_equal = any(lvl == sender_level for lvl in known)

    # mixed: üst + alt / farklı yönler
    if any_recipient_higher and any_recipient_lower:
        return "mixed"

    # Dağıtımda aynı anda eşit + alt/üst varsa da mixed sayalım
    if any_recipient_equal and (any_recipient_higher or any_recipient_lower):
        return "mixed"

    if any_recipient_lower:
        return "sender_higher"  # gönderici üst -> rica
    if any_recipient_higher:
        return "sender_lower"  # gönderici alt -> arz
    if any_recipient_equal:
        return "sender_equal"  # eşit -> arz

    return "unknown"


def decide_expected_closing(
    *,
    sender_title: Optional[str],
    recipients: list[str],
    distribution: list[str] | None,
    peer_distribution_as_mixed: bool,
    rules: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    distribution = distribution or []
    all_recipients_raw = [r for r in recipients if r] + [d for d in distribution if d]
    # Sıra korumalı dedupe
    seen: set[str] = set()
    all_recipients: list[str] = []
    for item in all_recipients_raw:
        key = _norm(item)
        if key in seen:
            continue
        seen.add(key)
        all_recipients.append(item)

    if not all_recipients:
        rel = "unknown"
        expected = rules["closing_rules"]["rules"][rel]["expected_closing"]
        return rel, expected, {"reason": "no_recipients"}

    # Kamu dışı tüzel kişi tespiti
    non_public_flags = [_is_non_public_entity(r, rules) for r in all_recipients]
    any_non_public = any(non_public_flags)
    any_public = any(not f for f in non_public_flags)

    # Dağıtım varsa ve kamu + kamu dışı karışık ise: karma alıcı, arz ve rica
    if len(all_recipients) > 1 and any_non_public and any_public:
        rel = "mixed"
        expected = rules["closing_rules"]["rules"][rel]["expected_closing"]
        return rel, expected, {"non_public_mix": True}

    # Tüm alıcılar kamu dışı ise: rica
    if any_non_public and not any_public:
        rel = "external_non_public"
        expected = rules["closing_rules"]["rules"][rel]["expected_closing"]
        return rel, expected, {"non_public": True}

    sender_level = _infer_level_from_title(sender_title, rules)
    rec_levels = [_infer_level_from_title(r, rules) for r in all_recipients]

    # Dağıtım karar mantığı (yeni kurallar):
    # TEKLİ ALICI:
    #   - Alt => Rica ederim
    #   - Üst => Arz ederim
    #   - Eşit => Arz ve rica ederim
    # ÇOKLU ALICI (5 senaryo):
    #   1. Hepsi alt => Rica ederim
    #   2. Hepsi üst => Arz ederim
    #   3. Eşit + Alt => Arz ve rica ederim
    #   4. Eşit + Üst => Arz ve rica ederim
    #   5. Alt + Üst => Arz ve rica ederim
    if sender_level is not None:
        known = [lvl for lvl in rec_levels if lvl is not None]
        any_higher = any(lvl < sender_level for lvl in known)  # alıcı daha üst
        any_lower = any(lvl > sender_level for lvl in known)  # alıcı daha alt
        any_equal = any(lvl == sender_level for lvl in known)
        has_multi_recipient = len(all_recipients) > 1

        if has_multi_recipient:
            # ÇOKLU ALICI
            if any_lower and (any_higher or any_equal):
                # Senaryo 3 veya 5: Alt + (Eşit veya Üst) => Arz ve rica
                rel = "mixed"
            elif any_equal and any_higher:
                # Senaryo 4: Eşit + Üst => Arz ve rica
                rel = "mixed"
            elif any_lower and not any_higher and not any_equal:
                # Senaryo 1: Hepsi alt => Rica
                rel = "sender_higher"
            elif any_higher and not any_lower and not any_equal:
                # Senaryo 2: Hepsi üst => Arz
                rel = "sender_lower"
            elif any_equal and not any_higher and not any_lower:
                # Hepsi eşit => peer_distribution_as_mixed kontrolü
                if peer_distribution_as_mixed:
                    rel = "mixed"  # Arz ve rica
                else:
                    rel = "sender_equal"  # Arz ederim
            else:
                rel = "unknown"
        else:
            # TEKLİ ALICI
            if any_lower:
                rel = "sender_higher"  # Rica ederim
            elif any_higher:
                rel = "sender_lower"  # Arz ederim
            elif any_equal:
                rel = "mixed"  # Arz ve rica ederim
            else:
                rel = "unknown"
    else:
        rel = _relation_from_levels(sender_level, rec_levels)

    expected = rules["closing_rules"]["rules"].get(rel, rules["closing_rules"]["rules"]["unknown"])[
        "expected_closing"
    ]

    return rel, expected, {
        "sender_title": sender_title,
        "sender_level": sender_level,
        "recipients": all_recipients,
        "recipient_levels": rec_levels,
    }


def _replace_or_append_closing(text: str, expected_closing: str) -> tuple[str, Optional[str]]:
    found = _find_closing_phrase(text)

    if found is None:
        # Metin sonuna ekle
        sep = "\n" if not text.endswith("\n") else ""
        return f"{text}{sep}{expected_closing}\n", None

    # Metinde bulunan kapanış ifadesini, beklenen tam kalıp (noktalı) ile değiştir.
    for pat in _CLOSING_PATTERNS:
        if pat.search(text):
            return pat.sub(expected_closing, text, count=1), found

    return text, found


def check_and_fix(*, text: str, meta: dict[str, Any], rules: dict[str, Any]) -> tuple[DecisionResult, str]:
    sender_title = meta.get("sender_title")

    # Dağıtım politikası:
    # - False (varsayılan): yönetmelik sıkı yorum -> eşit düzey dağıtımda "Arz ederim."
    # - True: kurum içi pratik -> eşit düzey birden fazla alıcıya dağıtımda "Arz ve rica ederim."
    peer_distribution_as_mixed = bool(meta.get("peer_distribution_as_mixed", False))

    recipients: list[str] = []
    if isinstance(meta.get("recipient"), str):
        recipients.append(meta["recipient"])
    if isinstance(meta.get("recipients"), list):
        recipients.extend([str(x) for x in meta["recipients"] if x is not None])

    distribution: list[str] = []
    if isinstance(meta.get("distribution"), list):
        distribution.extend([str(x) for x in meta["distribution"] if x is not None])

    relation, expected, dbg = decide_expected_closing(
        sender_title=sender_title,
        recipients=recipients,
        distribution=distribution,
        peer_distribution_as_mixed=peer_distribution_as_mixed,
        rules=rules,
    )

    found = _find_closing_phrase(text)
    current_ok = False
    if found is not None:
        found_n = _norm(found).rstrip(".")
        expected_n = _norm(expected).rstrip(".")
        current_ok = expected_n == found_n or expected_n in found_n

    forbidden = _detect_forbidden_phrases(text, rules)
    wrong_suggestions = _detect_wrong_words(text, meta.get("_word_maps", {}))

    suggested_fix: Optional[str] = None
    new_text = text
    
    # Yanlış kelimeleri düzelt
    new_text = _apply_word_corrections(new_text, meta.get("_word_maps", {}))
    
    # Kapanış ibaresini düzelt/ekle
    if not current_ok:
        new_text, _ = _replace_or_append_closing(new_text, expected)
        suggested_fix = expected

    result = DecisionResult(
        relation=relation,
        expected_closing=expected,
        found_closing=found,
        is_current_closing_ok=current_ok,
        suggested_fix=suggested_fix,
        forbidden_phrases_found=forbidden,
        wrong_word_suggestions=wrong_suggestions,
        debug=dbg,
    )

    return result, new_text


def build_llm_prompt(*, text: str, decision: DecisionResult) -> str:
    decision_json = json.dumps(asdict(decision), ensure_ascii=False, indent=2)

    # Fotoğraftaki "Bilgi/Gereği" kapanış şablonları. LLM, expected_closing ile biten birini seçmeli.
    closing_templates = [
        "Bilgilerinizi ve gereğini {closing}",
        "Bilgilerinizi {closing}",
        "Gereğini {closing}",
        "Gereğini bilgilerinize {closing}",
        "Bilgilerinizi ve gereğini arz/rica ederim.",
        "Gereğini arz ve rica ederim.",
        "Bilgilerinizi arz ve rica ederim.",
    ]
    closing_templates_text = "\n".join(f"- {t}" for t in closing_templates)

    return (
        "Rolün: Sağlık Bakanlığı DYS resmî yazışma format kontrol asistanısın.\n"
        "Amaç: Yazı metnini resmî yazışma yönetmeliğine uygun hale getirmek.\n\n"
        "Kritik kurallar:\n"
        "- DYS resmî yazışmasıdır; mail dili kullanılmaz (örn. 'İyi çalışmalar', 'Kolay gelsin' vb.).\n"
        "- Cümleler resmî yazışmaya uygun, mümkün olduğunca edilgen/kurumsal yapıda olmalıdır (örn. '... yapılmaktadır/edilmektedir/sağlanmaktadır').\n"
        "- Metnin anlamını ve içeriğini KORU. Gereksiz yeniden yazım yapma; mümkün olduğunca mevcut cümleleri koruyarak düzelt.\n"
        "- Başlık/kurum satırları, konu satırı, hitap satırı ve satır sonları/boşluklar mümkün olduğunca aynen korunmalıdır.\n"
        "- Metin sonu kapanış cümlesi hiyerarşi kararındaki expected_closing ile uyumlu olmalıdır.\n"
        "- Metin, mutlaka aşağıdaki KAPANIŞ ŞABLONLARINDAN BİRİ ile bitmelidir; {closing} yerine expected_closing koy.\n"
        "- expected_closing ifadesini (örn. 'Arz ederim.') tek başına ayrı satır olarak yazma; mutlaka şablon içinde kullan.\n\n"
        "KAPANIŞ ŞABLONLARI (birini seç):\n"
        f"{closing_templates_text}\n\n"
        "HİYERARŞİ KARARI (kural tabanlı JSON):\n"
        f"{decision_json}\n\n"
        "GÖREV:\n"
        "- Sadece gerekli düzeltmeleri öner.\n"
        "- Metin sonu arz/rica ibaresi yanlışsa düzelt.\n"
        "- Kural JSON'unda wrong_word_suggestions varsa doğru yazımıyla değiştir.\n"
        "- Mail dili (yasak ifadeler) varsa kaldır veya resmî karşılığına çevir.\n"
        "- İçerik aynı kalmalı: Yeni konu/amaç ekleme, metni başka bir doküman türüne dönüştürme.\n"
        "- Çıktı: Düzeltilmiş TAM METİN.\n\n"
        "METİN:\n"
        f"{text}"
    )


def call_lm_studio_chat_completion(
    *,
    base_url: str,
    model: str,
    prompt: str,
    timeout_seconds: float,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"LM Studio isteği başarısız: {e}")

    try:
        payload = json.loads(raw)
        return str(payload["choices"][0]["message"]["content"])
    except Exception as e:
        raise RuntimeError(f"LM Studio yanıtı parse edilemedi: {e}. Raw: {raw[:300]}")


def _enforce_closing_template(text: str, expected_closing: str) -> str:
    """LLM çıktısında kapanışın tek başına bırakılmasını engeller.

    Eğer son anlamlı satır sadece expected_closing ise, bunu yönetmelik örneklerine uygun
    bir şablon içine alır.
    """
    lines = text.splitlines()
    # sonda boş satırları temizle
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return text

    last = lines[i].strip()

    exp = expected_closing.strip()
    exp_no_dot = exp.rstrip(".")
    exp_in_sentence = exp[:1].lower() + exp[1:] if exp else exp

    def _is_standalone_closing(s: str) -> bool:
        s_clean = s.strip()
        s_no_dot = s_clean.rstrip(".")
        if not s_no_dot:
            return False
        if _norm(s_no_dot) != _norm(exp_no_dot):
            return False
        return _norm(s_no_dot) in {
            "arz ederim",
            "rica ederim",
            "arz ve rica ederim",
            "arz/rica ederim",
        }

    def _choose_template(prefix_context: str) -> str:
        ctx = _norm(prefix_context)
        if any(k in ctx for k in ["talep olunur", "talep edilmistir", "talep edilmiştir", "yapilmasi hususu", "yapılması hususu"]):
            return "Gereğini bilgilerinize {closing}"
        if any(k in ctx for k in ["bilgisi sunulur", "bilgilerinize sunulur", "bilgilerinize arz olunur"]):
            return "Bilgilerinizi {closing}"
        return "Bilgilerinizi ve gereğini {closing}"

    if _is_standalone_closing(last):
        prefix_context = "\n".join(lines[max(0, i - 8) : i + 1])
        tmpl = _choose_template(prefix_context)
        lines[i] = tmpl.format(closing=exp_in_sentence)
        return "\n".join(lines)

    # Model bazen şablon içinde ama fazla kısa bir kapanış üretebilir: "Gereğini arz ederim." gibi.
    # Bu durumda (özellikle talep/husus bağlamında) daha resmî ve akıcı formu tercih edelim.
    bare_geregini = re.compile(r"(?i)^\s*gereğini\s+(arz\s+ederim|rica\s+ederim|arz\s+ve\s+rica\s+ederim|arz\s*/\s*rica\s+ederim)\s*\.?\s*$")
    if bare_geregini.match(last) and exp:
        # Beklenen kapanışın cümle içi (küçük harf) versiyonu ile standardize et.
        lines[i] = f"Gereğini bilgilerinize {exp_in_sentence}"
        return "\n".join(lines)

    return text


def _normalize_closing_phrase_case(text: str, expected_closing: str) -> str:
    """Metin içinde kapanış ibaresini expected_closing biçimine normalize eder."""
    exp = expected_closing.strip()
    if not exp:
        return text

    # Beklenen ibareyi (noktasız) türet ve olası varyantları yakala.
    exp_no_dot = exp.rstrip(".")

    # Sadece kapanış ibaresini hedefleyelim.
    candidates = [
        r"arz\s+ederim",
        r"rica\s+ederim",
        r"arz\s+ve\s+rica\s+ederim",
        r"arz\s*/\s*rica\s+ederim",
    ]
    pattern = re.compile(r"(?i)\b(" + "|".join(candidates) + r")\b\.?\s*$")

    lines = text.splitlines()
    # sondan ilk dolu satıra uygula
    for idx in range(len(lines) - 1, -1, -1):
        if not lines[idx].strip():
            continue
        if not pattern.search(lines[idx]):
            break
        # Noktasız dönüş varsa exp'nin noktasızını kullan.
        replacement = exp if lines[idx].rstrip().endswith(".") else (exp_no_dot + ".")
        lines[idx] = pattern.sub(replacement, lines[idx])
        break

    return "\n".join(lines)


def _read_text(path: Optional[str]) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DYS resmî yazışma: arz/rica kapanış kontrolü + otomatik düzeltme (kural tabanlı)."
    )
    parser.add_argument(
        "--text",
        help="Metin dosyası yolu (utf-8). Verilmezse stdin kullanılır.",
        default=None,
    )
    parser.add_argument(
        "--meta",
        help=(
            "Gönderen/alıcı bilgisi JSON dosyası yolu. Örn: {sender_title, recipient, recipients, distribution}"
        ),
        required=True,
    )
    parser.add_argument(
        "--rules",
        help="hierarchy_rules.json yolu",
        default="hierarchy_rules.json",
    )
    parser.add_argument(
        "--word-maps",
        help="word_maps.json yolu (sık yanlış kelimeler için)",
        default="word_maps.json",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="LLM prompt şablonunu da bas",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="LM Studio (OpenAI uyumlu) üzerinden LLM ile düzeltilmiş TAM metin üret",
    )
    parser.add_argument(
        "--llm-base-url",
        default="http://localhost:1234/v1",
        help="LM Studio base url (örn. http://localhost:1234/v1)",
    )
    parser.add_argument(
        "--llm-model",
        default="gemma-3-12b-it",
        help="LM Studio model adı (örn. gemma-3-12b-it)",
    )
    parser.add_argument(
        "--llm-timeout",
        default=120.0,
        type=float,
        help="LLM çağrısı timeout (saniye)",
    )
    parser.add_argument(
        "--out-original",
        help="Orijinal metni bu dosyaya yaz (bozuk_metin.txt)",
        default=None,
    )
    parser.add_argument(
        "--out-fixed",
        help="Düzeltilmiş metni bu dosyaya yaz (yeni_metin.txt)",
        default=None,
    )
    parser.add_argument(
        "--out-llm",
        help="LLM'in ürettiği düzeltilmiş TAM metni bu dosyaya yaz",
        default=None,
    )

    args = parser.parse_args()

    rules = _load_rules(Path(args.rules))
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    word_maps = _load_word_maps(Path(args.word_maps))

    # check_and_fix'e ekstra veri taşımak için meta içine gömüyoruz (kullanım basit kalsın).
    meta["_word_maps"] = word_maps

    text = _read_text(args.text)

    decision, fixed = check_and_fix(text=text, meta=meta, rules=rules)

    print("=" * 80)
    print("ORİJİNAL METİN")
    print("=" * 80)
    print(text)

    if args.out_original:
        Path(args.out_original).write_text(text, encoding="utf-8")

    print("\n" + "=" * 80)
    print("KURAL SONUCU (JSON)")
    print("=" * 80)
    print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))

    print("\n" + "=" * 80)
    print("DÜZELTİLMİŞ METİN")
    print("=" * 80)
    print(fixed)

    if args.out_fixed:
        Path(args.out_fixed).write_text(fixed, encoding="utf-8")

    if args.use_llm:
        prompt = build_llm_prompt(text=text, decision=decision)
        llm_text = call_lm_studio_chat_completion(
            base_url=args.llm_base_url,
            model=args.llm_model,
            prompt=prompt,
            timeout_seconds=float(args.llm_timeout),
        )
        llm_text = _enforce_closing_template(llm_text, decision.expected_closing)
        llm_text = _normalize_closing_phrase_case(llm_text, decision.expected_closing)
        print("\n" + "=" * 80)
        print("LLM DÜZELTİLMİŞ TAM METİN")
        print("=" * 80)
        print(llm_text)
        if args.out_llm:
            Path(args.out_llm).write_text(llm_text, encoding="utf-8")

    if args.print_prompt:
        print("\n" + "=" * 80)
        print("LLM PROMPT")
        print("=" * 80)
        print(build_llm_prompt(text=text, decision=decision))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
