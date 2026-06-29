# 📝 DYS Resmi Yazışma Düzeltici (Arz/Rica & Kelime Kontrolü)

Resmî yazışmalarda hiyerarşi kurallarına, kelime doğruluğuna ve kurumsal üsluba göre otomatik metin analizi ve düzeltmesi yapan gelişmiş bir web uygulamasıdır.

Bu proje, hem **kural-tabanlı (rule-based) analiz** hem de yerel **LLM (LM Studio / Gemma 3 vb.) entegrasyonu** kullanarak hatalı resmi yazışmaları Kamu Kurumları Resmi Yazışma Yönetmeliği standartlarına uygun hale getirir.

---

## 🎯 Temel Özellikler

1. **Hiyerarşi Analizi**: Gönderen ve alıcı makamların yetki düzeylerini karşılaştırarak doğru kapanış ibaresini ("Arz ederim", "Rica ederim", "Arz ve rica ederim") belirler.
2. **Kelime Düzeltme Önerileri**: Türkçe imla kurallarına uymayan veya resmi yazışmalarda yanlış kullanılan kelimeleri otomatik tespit eder ve düzeltir.
3. **Yasak İfade Tespiti**: Resmi yazışma üslubuna uymayan "iyi çalışmalar", "teşekkürler" gibi mail/günlük konuşma dili kalıplarını bulur.
4. **LLM Entegrasyonu**: Kural motorundan gelen analiz sonuçlarını sistem yönergesine dahil ederek, metnin anlamını bozmadan resmi dile uygun şekilde yeniden yazılmasını sağlar.
5. **Gelişmiş Streamlit Arayüzü**: Kullanıcıların tekli veya çoklu alıcıları seçebildiği, sonuçları karşılaştırmalı olarak görebildiği modern bir arayüz sunar.

---

## 📂 Proje Yapısı

```
dys-2-streamlit_demo/
├── app.py                      # Streamlit web arayüzü
├── dys2_arz_rica_checker.py   # Kural motoru, LLM istemcisi ve ana mantık
├── hierarchy_rules.json        # Hiyerarşi düzeyleri ve kapanış kuralları (JSON)
├── word_maps.json             # Yanlış/Doğru kelime eşleme veri tabanı (JSON)
├── PRD.md                     # Ürün Gereksinimleri Dokümanı
├── YAPILACAKLAR.md            # Geliştirme yapılacaklar listesi
├── README.md                  # Proje genel dökümantasyonu (Bu dosya)
└── *.txt / *.json             # Test senaryoları ve veri örnekleri
```

---

## ⚙️ Gereksinim Duyulan JSON Yapılandırmaları

Uygulamanın düzgün çalışması ve prompt oluşturma süreçlerinde yönlendirme yapabilmesi için iki temel yapılandırma dosyası kullanılır:

### 1. `hierarchy_rules.json`
Bu dosya, makamların hiyerarşik ağırlıklarını, kapanış kurallarını, yetki devri belirteçlerini ve yasak ifadeleri tanımlar.

* **`hierarchy_levels.levels`**: Unvanların hiyerarşik seviyesini gösterir (Küçük sayı = Daha yüksek yetki).
  | Unvan | Hiyerarşik Seviye |
  | :--- | :---: |
  | Cumhurbaşkanlığı | 1 |
  | TBMM | 2 |
  | Bakanlık / Bakan | 3 |
  | Bakan Yardımcılığı / Müsteşarlık | 4 |
  | Genel Müdürlük / Başkanlık | 5 |
  | Genel Müdür Yardımcılığı | 6 |
  | Daire Başkanlığı | 7 |
  | Koordinatörlük / Şube Müdürlüğü | 8 |
  | Birim Sorumluluğu | 9 |
  | Uzman / Memur | 10 |

* **`closing_rules.rules`**: İlişki tiplerine göre beklenen kapanış ifadelerini belirler:
  - **`sender_higher`** (Üstten Alta): `"rica ederim."`
  - **`sender_lower`** (Alttan Üste): `"arz ederim."`
  - **`sender_equal`** (Eşit Düzey): `"arz ederim."`
  - **`mixed`** (Karma Dağıtım - Üst+Alt veya Eşit+Alt): `"arz ve rica ederim."`
  - **`external_non_public`** (Kamu Dışı Kuruluşlar): `"rica ederim."`
  - **`unknown`** (Belirsiz): `"arz ederim."`

* **`external_entity_keywords`**: Kamu dışı özel tüzel kişilikleri (örn. "ltd.", "a.ş.", "vakfı", "holding", "özel hastane") belirlemek için kullanılan anahtar kelimeleri içerir.
* **`forbidden_phrases`**: Resmi dilde kullanılmaması gereken mail jargonu kalıplarını (örn. "iyi çalışmalar", "kolay gelsin", "teşekkürler") barındırır.

### 2. `word_maps.json`
Bu dosya, sık yapılan yazım hatalarını ve bunların resmi yazışma standartlarındaki doğru karşılıklarını eşler.

* **`wrong_to_correct.mappings`**:
  - `milletler arası` ➜ `milletlerarası`
  - `döküman` ➜ `doküman`
  - `herkez` ➜ `herkes`
  - `yurtdışı` ➜ `yurt dışı`
  - `birşey` ➜ `bir şey`
  - `işbirliği` ➜ `iş birliği`
  - `ayrıyeten` ➜ `ayrıca`

---

## 🤖 LLM Prompt Entegrasyonu ve Çalışma Mantığı

Kural-tabanlı motor (`check_and_fix`) metni analiz edip şu JSON çıktısını üretir:
```json
{
  "relation": "sender_higher",
  "expected_closing": "rica ederim.",
  "found_closing": "İyi çalışmalar.",
  "is_current_closing_ok": false,
  "suggested_fix": "rica ederim.",
  "forbidden_phrases_found": ["iyi çalışmalar"],
  "wrong_word_suggestions": [
    {"wrong": "döküman", "correct": "doküman"},
    {"wrong": "herkez", "correct": "herkes"}
  ]
}
```

Bu JSON verisi, arka planda dinamik olarak hazırlanan **LLM Prompt şablonuna** (`HİYERARŞİ KARARI` başlığı altına) gömülür. Prompt içerisinde modele şu kurallar dikte edilir:
- Metnin orijinal anlamını ve içeriğini kesinlikle **koru**.
- Kural JSON'undaki `wrong_word_suggestions` alanındaki kelimeleri doğru yazımlarıyla değiştir.
- `forbidden_phrases_found` alanındaki gayriresmi veya mail dili ifadelerini kaldır ya da kurumsal dile çevir.
- Son satırdaki kapanış ibaresini, belirlenen `expected_closing` ifadesini içeren resmi bir şablona (örn: *"Bilgilerinizi ve gereğini rica ederim."*) dönüştür.

---

## 🧪 Test Senaryoları ve Düzeltilmiş Örnekler

Aşağıda uygulamada test edilen ve doğrulanan iki farklı resmi yazışma senaryosu yer almaktadır:

### Senaryo 1: Üst Makamdan Alt Makama (S1_ONLY_LOWER)
* **Gönderen**: Daire Başkanı (Seviye 7)
* **Alıcı**: Şube Müdürlüğü (Seviye 8)
* **Hiyerarşik Beklenti**: Rica ederim.

| Durum | Metin İçeriği |
| :--- | :--- |
| **Orijinal (Bozuk) Metin** | T.C.<br>SAĞLIK BAKANLIĞI<br>Sağlık Bilgi Sistemleri Genel Müdürlüğü<br>Konu : VPN Hesabı Açılması<br>ŞUBE MÜDÜRLÜĞÜNE<br><br>Ben size bu dökümanları gönderiyorum, sen de açıp bakar mısın? Ayrıyeten herkez görsün diye birime paylaştım.<br>VPN hesabı açılması ile ilgili işlem yapılması lazım.<br><br>**İyi çalışmalar.** |
| **Kural Tabanlı Düzeltilmiş** | T.C.<br>SAĞLIK BAKANLIĞI<br>Sağlık Bilgi Sistemleri Genel Müdürlüğü<br>Konu : VPN Hesabı Açılması<br>ŞUBE MÜDÜRLÜĞÜNE<br><br>Ben size bu dokümanları gönderiyorum, sen de açıp bakar mısın? Ayrıca herkes görsün diye birime paylaştım.<br>VPN hesabı açılması ile ilgili işlem yapılması lazım.<br><br>**Rica ederim.** |
| **LLM Düzeltilmiş (AI)** | T.C.<br>SAĞLIK BAKANLIĞI<br>Sağlık Bilgi Sistemleri Genel Müdürlüğü<br>Konu : VPN Hesabı Açılması<br>ŞUBE MÜDÜRLÜĞÜNE<br><br>Bu dokümanlar tarafınızca incelenmesi rica olunur. Ayrıca, ilgili birim personeli tarafından da değerlendirilmesi için paylaşıldığı bilgisi sunulmuştur.<br>VPN hesabı açılması ile ilgili işlemlerin gerçekleştirilmesi gerekmektedir.<br><br>**Bilgilerinizi ve gereğini rica ederim.** |

---

### Senaryo 2: Alt Makamdan Üst Makama (S2_ONLY_UPPER)
* **Gönderen**: Şube Müdürü (Seviye 8)
* **Alıcı**: Daire Başkanlığı (Seviye 7)
* **Hiyerarşik Beklenti**: Arz ederim.

| Durum | Metin İçeriği |
| :--- | :--- |
| **Orijinal (Bozuk) Metin** | T.C.<br>SAĞLIK BAKANLIĞI<br>Konu : Eğitim Planı<br>DAİRE BAŞKANLIĞINA<br><br>Sana daha önce konuştuğumuz proğramı yolluyorum. Ben bunu hazırladım, sen de onay verir misin?<br>Bu gün içinde dönüş yaparsanız sevinirim.<br><br>**Kolay gelsin.** |
| **Kural Tabanlı Düzeltilmiş** | T.C.<br>SAĞLIK BAKANLIĞI<br>Konu : Eğitim Planı<br>DAİRE BAŞKANLIĞINA<br><br>Sana daha önce konuştuğumuz programı yolluyorum. Ben bunu hazırladım, sen de onay verir misin?<br>Bu gün içinde dönüş yaparsanız sevinirim.<br><br>**Arz ederim.** |
| **LLM Düzeltilmiş (AI)** | T.C.<br>SAĞLIK BAKANLIĞI<br>Konu : Eğitim Planı<br>DAİRE BAŞKANLIĞINA,<br><br>Ayrıntıları aşağıda yer alan eğitim planı tarafımızca hazırlanmıştır. Söz konusu planın değerlendirilerek onaylanması hususu bilgilerinize sunulmuştur.<br>Bugün içerisinde konuyla ilgili bir görüş alınması beklenmektedir.<br><br>**Bilgilerinizi ve gereğini arz ederim.** |

---

## 🚀 Kurulum ve Çalıştırma

### 1. Kütüphaneleri Yükleyin
```bash
pip install streamlit
```

### 2. LM Studio Kurulumu (İsteğe Bağlı - AI Düzeltme İçin)
1. [LM Studio](https://lmstudio.ai/) uygulamasını bilgisayarınıza indirin ve kurun.
2. Uygulama içerisinden resmi yazışmaya uygun bir model indirin (Örn: `gemma-3-12b-it` veya `llama-3`).
3. **Local Server** sekmesinden sunucuyu başlatın (Varsayılan URL: `http://localhost:1234/v1`).

### 3. Uygulamayı Başlatın
```bash
streamlit run app.py
```
Arayüze web tarayıcınızdan `http://localhost:8501` adresinden erişebilirsiniz.

---

## 🤝 Katkıda Bulunma
Hatalı veya eksik hiyerarşik rolleri eklemek için `hierarchy_rules.json` dosyasını, yeni kelime düzeltmeleri eklemek için ise `word_maps.json` dosyasını güncelleyebilir ve bir Pull Request oluşturabilirsiniz.


