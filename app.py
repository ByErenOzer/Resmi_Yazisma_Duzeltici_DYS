from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import streamlit as st

from dys2_arz_rica_checker import (
    build_llm_prompt,
    call_lm_studio_chat_completion,
    check_and_fix,
    _enforce_closing_template,
    _normalize_closing_phrase_case,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))




def _build_role_options(rules: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Kullanıcı tarafından belirlenen temel unvanları hiyerarşik sırayla döndürür.
    """
    # Kullanıcının belirlediği temel unvanlar (hiyerarşik sırayla)
    predefined_roles = [
        ("cumhurbaşkanlığı", 1, "Cumhurbaşkanlığı"),
        ("tbmm", 2, "TBMM"),
        ("bakanlık", 3, "Bakanlık"),
        ("bakan yardımcılığı", 4, "Bakan Yardımcılığı"),
        ("genel müdürlük", 5, "Genel Müdürlük"),
        ("genel müdür yardımcılığı", 6, "Genel Müdür Yardımcılığı"),
        ("daire başkanlığı", 7, "Daire Başkanlığı"),
        ("koordinatörlük", 8, "Koordinatörlük"),
        ("şube müdürlüğü", 8, "Şube Müdürlüğü"),
        ("birim sorumluluğu", 9, "Birim Sorumluluğu"),
        ("uzman", 10, "Uzman"),
        ("memur", 10, "Memur"),
    ]
    
    options: list[tuple[str, str]] = []
    for key, level, display_name in predefined_roles:
        # (internal_key, display_label)
        options.append((key, f"{display_name} (Seviye {level})"))
    
    return options


def main() -> None:
    st.set_page_config(
        page_title="DYS Arz/Rica Metin Düzenleyici",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    base_dir = Path(__file__).resolve().parent
    rules_path = base_dir / "hierarchy_rules.json"
    word_maps_path = base_dir / "word_maps.json"

    if not rules_path.exists():
        st.error(f"❌ Kurallar dosyası bulunamadı: {rules_path}")
        st.stop()

    rules = _load_json(rules_path)
    word_maps = _load_json(word_maps_path) if word_maps_path.exists() else {}

    role_options = _build_role_options(rules)
    if not role_options:
        st.error("❌ Rol seçenekleri üretilemedi (hierarchy_levels.levels boş olabilir).")
        st.stop()

    st.title("📝 DYS Arz/Rica Metin Düzenleyici")
    st.markdown(
        """
        **Resmî yazışmalarda hiyerarşi kurallarına göre otomatik metin düzeltme aracı**
        
        Bu uygulama:
        - Gönderen/alıcı hiyerarşisine göre doğru kapanış ibaresini belirler (Arz/Rica)
        - Sık yapılan yazım hatalarını tespit eder
        - Yasak/informel ifadeleri kontrol eder
        - İsteğe bağlı LLM ile tam metin düzeltme yapar
        """
    )
    st.divider()

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("📥 Girdi")

        default_text = ""
        sample_path = base_dir / "bozuk_metin1.txt"
        if sample_path.exists():
            try:
                default_text = sample_path.read_text(encoding="utf-8")
            except Exception:
                default_text = ""

        text = st.text_area(
            "Düzeltilecek Metin",
            value=default_text,
            height=300,
            help="Resmî yazışma metnini buraya yapıştırın",
        )

        st.markdown("#### 👤 Gönderen/Alıcı Bilgileri")
        
        sender_key, sender_label = st.selectbox(
            "Kimden (Gönderen makam)",
            options=role_options,
            format_func=lambda x: x[1],
            help="Yazıyı gönderen makamın unvanını seçin",
        )

        recipient_mode = st.radio(
            "Alıcı Seçim Modu",
            options=["Tekli", "Çoklu"],
            horizontal=True,
            help="Tek alıcı mı yoksa birden fazla alıcı mı?",
        )

        if recipient_mode == "Tekli":
            recipient_single = st.selectbox(
                "Kime (Alıcı makam)",
                options=role_options,
                index=0,
                format_func=lambda x: x[1],
                help="Yazıyı alacak makamın unvanını seçin",
            )
            recipients_multi = None
        else:
            recipient_single = None
            recipients_multi = st.multiselect(
                "Kime (Alıcı makam/lar)",
                options=role_options,
                default=[role_options[0]] if role_options else [],
                format_func=lambda x: x[1],
                help="Yazıyı alacak makamların unvanlarını seçin (birden fazla)",
            )

        st.markdown("#### ⚙️ Kontrol Seçenekleri")
        
        peer_distribution_as_mixed = st.checkbox(
            "Eşit düzey birden fazla alıcıda 'Arz ve rica' kullan (kurum içi pratik)",
            value=False,
            help="Aynı seviyede birden fazla alıcıya gönderimde 'Arz ve rica ederim' kullanılır",
        )

        st.divider()
        run = st.button("🚀 Metni Düzenle", type="primary", use_container_width=True)

    with right:
        st.subheader("📤 Çıktı")

        if run:
            if not text or not text.strip():
                st.warning("⚠️ Lütfen düzeltilecek metni girin.")
                st.stop()

            meta: dict[str, Any] = {
                "sender_title": sender_key,
                "peer_distribution_as_mixed": bool(peer_distribution_as_mixed),
                "_word_maps": word_maps,
            }

            if recipient_mode == "Tekli":
                if recipient_single:
                    meta["recipient"] = recipient_single[0]
            else:
                if recipients_multi:
                    meta["recipients"] = [r[0] for r in recipients_multi]
                else:
                    st.warning("⚠️ Lütfen en az bir alıcı seçin.")
                    st.stop()

            with st.spinner("🔄 Metin düzenleniyor..."):
                decision, fixed_text = check_and_fix(text=text, meta=meta, rules=rules)

            col_out1, col_out2 = st.columns(2, gap="medium")
            
            with col_out1:
                st.success("✅ Kural-tabanlı düzeltme tamamlandı!")
                st.text_area(
                    "📄 Yeni Metin (Kural-tabanlı)",
                    value=fixed_text,
                    height=300,
                    help="Hiyerarşi kurallarına göre düzeltilmiş metin",
                    key="output_rule_based",
                )
            
            with col_out2:
                llm_base_url = "http://localhost:1234/v1"
                llm_model = "gemma-3-12b-it"
                llm_timeout = 120
                
                try:
                    with st.spinner("🤖 LLM ile düzeltiliyor..."):
                        prompt = build_llm_prompt(text=text, decision=decision)
                        llm_text = call_lm_studio_chat_completion(
                            base_url=llm_base_url,
                            model=llm_model,
                            prompt=prompt,
                            timeout_seconds=llm_timeout,
                        )
                        llm_text = _enforce_closing_template(llm_text, decision.expected_closing)
                        llm_text = _normalize_closing_phrase_case(llm_text, decision.expected_closing)
                    
                    st.success("✅ LLM düzeltme tamamlandı!")
                    st.text_area(
                        "🤖 Yeni Metin (LLM ile Düzeltilmiş)",
                        value=llm_text,
                        height=300,
                        help="AI tarafından tam düzeltilmiş metin",
                        key="output_llm",
                    )
                except Exception as e:
                    st.warning("⚠️ LLM düzeltme yapılamadı")
                    st.text_area(
                        "🤖 Yeni Metin (LLM ile Düzeltilmiş)",
                        value=f"LLM bağlantısı kurulamadı.\n\nHata: {e}\n\nLM Studio'nun çalıştığından ve model yüklü olduğundan emin olun.\n\nURL: {llm_base_url}\nModel: {llm_model}",
                        height=300,
                        help="LLM bağlantı hatası",
                        key="output_llm_error",
                        disabled=True,
                    )

            with st.expander("📊 Detaylı Sonuç Bilgisi", expanded=False):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**Hiyerarşi İlişkisi:**")
                    relation_map = {
                        "sender_higher": "🔽 Üstten Alta (Gönderen üst makam)",
                        "sender_lower": "🔼 Alttan Üste (Gönderen alt makam)",
                        "sender_equal": "↔️ Eşit Düzey",
                        "mixed": "🔀 Karma Dağıtım (Üst+Alt)",
                        "external_non_public": "🏢 Kamu Dışı Tüzel Kişi",
                        "unknown": "❓ Belirsiz",
                    }
                    st.info(relation_map.get(decision.relation, decision.relation))
                    
                    st.markdown("**Beklenen Kapanış:**")
                    st.success(f"✓ {decision.expected_closing}")
                    
                    if decision.found_closing:
                        st.markdown("**Bulunan Kapanış:**")
                        if decision.is_current_closing_ok:
                            st.success(f"✓ {decision.found_closing} (Doğru)")
                        else:
                            st.error(f"✗ {decision.found_closing} (Yanlış)")
                    else:
                        st.warning("⚠️ Metinde kapanış ibaresi bulunamadı")
                
                with col2:
                    if decision.forbidden_phrases_found:
                        st.markdown("**🚫 Yasak İfadeler:**")
                        for phrase in decision.forbidden_phrases_found:
                            st.error(f"✗ {phrase}")
                    else:
                        st.markdown("**✅ Yasak İfade Yok**")
                    
                    if decision.wrong_word_suggestions:
                        st.markdown("**📝 Kelime Düzeltme Önerileri:**")
                        for sugg in decision.wrong_word_suggestions:
                            st.warning(f"'{sugg['wrong']}' → '{sugg['correct']}'")
                    else:
                        st.markdown("**✅ Kelime Hatası Yok**")
                
                st.divider()
                st.markdown("**🔍 Tam Debug Bilgisi (JSON):**")
                st.json(decision.__dict__, expanded=False)
        else:
            st.info("👈 Soldan metni girin, gönderen/alıcı seçimlerini yapın ve 'Metni Düzenle' butonuna basın.")


if __name__ == "__main__":
    main()
