# EasyListing / kolaylistele — Sistem Özeti

> Teknik altyapı sunumu · Haziran 2026

---

## Bu Uygulama Ne Yapıyor?

Bir satıcı hayal edin: ürününün fotoğrafını çekiyor, bu uygulamaya yüklüyor ve birkaç saniye içinde Etsy, Trendyol, Amazon gibi platformlar için hazır bir ürün ilanı elde ediyor — başlık, açıklama, etiketler, fiyat önerisi hepsi dahil. Yapay zeka ürünü "görüp" analiz ediyor ve metni otomatik yazıyor.

İşte bu, **kolaylistele.com** ve **easylisting.app**'in yaptığı şey.

---

## İki Farklı Pazar, Tek Uygulama

| Alan Adı | Pazar | Para Birimi | Dil |
|----------|-------|-------------|-----|
| `kolaylistele.com` | Türkiye | ₺ TL | Türkçe |
| `easylisting.app` | Uluslararası | € Euro | İngilizce / Almanca |

Her iki domain de **aynı kodu** çalıştırıyor. Uygulama, hangi adrese bağlandığınıza göre dili, fiyatları ve e-posta şablonlarını otomatik ayarlıyor.

---

## Desteklenen Platformlar

Uygulama şu platformlar için ilan üretebiliyor:

**Etsy · Shopify · Amazon · eBay · WooCommerce · Pinterest · Trendyol · Hepsiburada · n11**

---

## Teknik Altyapı (Kısaca)

```
Kullanıcı Tarayıcısı / iOS Uygulaması
         ↓
    Cloudflare (DDoS koruması, CDN, SSL)
         ↓
  Railway PaaS (sunucu barındırma)
    └── Python + Flask uygulaması
         ├── SQLite veritabanı
         ├── Yapay zeka sağlayıcıları (Google, NVIDIA, OpenAI)
         ├── Stripe (ödeme)
         ├── Resend (e-posta)
         └── PostHog (analitik)
```

### Kullanılan Teknolojiler

| Ne? | Hangi teknoloji? |
|-----|-----------------|
| Backend dili | Python 3.12 |
| Web framework | Flask |
| Veritabanı | SQLite (Railway Volume'da kalıcı) |
| Sunucu | Gunicorn (Railway PaaS) |
| CDN / Güvenlik | Cloudflare |
| Birincil yapay zeka | Google Gemini 2.5 Flash |
| Yedek yapay zeka | NVIDIA NIM (ücretsiz) |
| 3. seçenek yapay zeka | OpenAI GPT-4o (ücretli, isteğe bağlı) |
| Fotoğraf üretimi | fal.ai — FLUX.1 Kontext |
| Ödeme sistemi | Stripe |
| E-posta | Resend API |
| Analitik | PostHog (EU sunucu) |
| Mobil uygulama | iOS (Swift / Xcode) |

---

## Kullanıcı Türleri ve Giriş Yöntemleri

Uygulamaya giriş yapmanın 3 yolu var:

### 1. Misafir (Guest) Kullanım
Hiçbir şeye kayıt olmadan direk kullanım. Tarayıcı parmak izi sistemi sayesinde sekmeleri kapatıp açsanız bile kullanım hakkınız korunuyor. Aylık **3 ücretsiz** ilan hakkı var.

### 2. E-posta ile Giriş (Magic Link)
Şifre yok — e-posta adresinizi yazıyorsunuz, size bir bağlantı geliyor, tıklıyorsunuz, oturumunuz açılıyor. E-posta hiçbir zaman düz metin olarak saklanmıyor, sadece SHA-256 özeti tutuluyor.

### 3. Etsy Hesabı ile Bağlantı (OAuth 2.0)
Etsy mağazanızı bağladığınızda, üretilen ilanları doğrudan Etsy'ye tek tıkla taslak olarak gönderebiliyorsunuz. Bağlantı güvenli OAuth 2.0 + PKCE protokolüyle yapılıyor.

**iOS uygulaması için** ayrıca bir token sistemi var — uygulama her istekte `X-Mobile-Token` başlığıyla kimlik doğruluyor.

---

## Yapay Zeka Nasıl Çalışıyor?

Kullanıcı fotoğraf yükleyince şu akış başlıyor:

```
Fotoğraf + ipucu metni
        ↓
① Google Gemini 2.5 Flash  ← önce bunu dene
        ↓ (kota doluysa)
② NVIDIA NIM (ücretsiz yedek)
        ↓ (o da doluysa)
③ OpenAI GPT-4o (ücretli, isteğe bağlı)
        ↓
Yapay zekanın ürettiği JSON yanıtı
        ↓
Platform özelleştirmesi (Etsy kategorisi, kargo profili vb.)
        ↓
Kullanıcıya göster
```

Her platform için ayrı bir "uzman metin yazarı" talimatı (prompt) var. Etsy için SEO kuralları, Amazon için karakter limitleri, Trendyol için Türkçe zorunluluğu gibi.

### Fotoğraf Üretimi (Pro Plan)
Pro aboneler, mevcut ürün fotoğrafından 3 farklı profesyonel versiyon üretebiliyor:
1. Beyaz arka plan (stüdyo çekimi)
2. Lifestyle (doğal ışık, minimal iç mekan)
3. Hediye temalı (altın saat ışığı, hediye kutusu atmosferi)

Bu fal.ai / FLUX.1 Kontext teknolojisiyle yapılıyor. Ayda 30 fotoğraf hakkı var.

---

## Ödeme Sistemi ve Planlar

| Plan | EUR/ay | TL/ay | Ne sunuyor? |
|------|--------|-------|-------------|
| Ücretsiz | — | — | Ayda 3 ilan, 1 geliştirme |
| Starter | €4.99 | ₺249 | Sınırsız ilan + geliştirme, toplu üretim, çeviri |
| Pro | €9.99 | ₺499 | Starter'daki her şey + ayda 30 AI fotoğraf |

Ödemeler **Stripe** ile yapılıyor. Abonelik iptallerini Stripe webhook'ları otomatik algılıyor ve hesabı düşürüyor — manuel müdahale gerekmiyor.

---

## Trendyol Entegrasyonu

Trendyol, Etsy gibi OAuth yerine API anahtarıyla bağlanıyor:

1. Kullanıcı Tedarikçi No + API Key + API Secret giriyor
2. Sistem Trendyol API'sine bağlanarak doğrulama yapıyor
3. Kategori ağacı, marka listesi, adres bilgileri otomatik çekiliyor
4. Üretilen ilan Trendyol'a gönderilebiliyor — ürün görselleri sunucumuza kaydedilip Trendyol'un okuyabileceği public URL'ler üretiliyor

---

## Veritabanı

Tek bir SQLite dosyası, Railway'de kalıcı bir disk alanında (Volume) tutuluyor. Ana tablolar:

| Tablo | Ne saklıyor? |
|-------|-------------|
| `shops` | Her kullanıcı/mağaza kaydı, plan, kullanım sayacı, Stripe bilgileri |
| `templates` | Mağazanın kaydettiği stil şablonu (marka tonu, etiketler, fiyat vb.) |
| `magic_links` | E-posta giriş tokenleri (15 dk geçerli, tek kullanımlık) |
| `verified_emails` | Doğrulanmış e-posta hash'leri → mağaza ID eşlemesi |
| `mobile_tokens` | iOS uygulaması oturum tokenleri |
| `fp_sessions` | Parmak izi → e-posta mağaza eşlemesi (oturum geri yükleme için) |
| `marketing_consents` | E-posta pazarlama izinleri ve abonelik iptalleri |
| `platform_credentials` | Trendyol gibi 3. taraf platform kimlik bilgileri |
| `abuse_signals` | Kötüye kullanım olayları log'u |

---

## Güvenlik Katmanları

Uygulama 6 katmanlı bir güvenlik mimarisi kullanıyor:

1. **Cloudflare** — DDoS, bot filtreleme, SSL
2. **Probe Path Engelleme** — `/.git`, `/.env`, `/wp-admin` gibi scanner sorgularını 404 ile yanıtlıyor
3. **CSRF Koruması** — Her state-değiştiren istek için token doğrulaması
4. **Rate Limiting** — Endpoint başına istek sınırları (örn: ilan üretimi dakikada 10)
5. **Magic Byte Doğrulama** — Yüklenen dosyaların gerçekten resim olup olmadığını binary seviyede kontrol ediyor
6. **Güvenlik Header'ları** — CSP, HSTS, X-Frame-Options, Permissions-Policy vb.

---

## Analitik

**PostHog** (AB sunucuları, GDPR uyumlu) kullanılıyor:
- Sayfa görüntüleme ve kullanıcı etkileşimleri otomatik yakalanıyor
- Plan yükseltme / iptal olayları sunucu tarafında da kaydediliyor
- Kullanıcı kimliği mağaza ID'siyle eşleştirilip segmentasyon yapılabiliyor

---

## Admin Paneli

`/admin/*` altında token korumalı yönetim araçları var:

| Endpoint | Ne yapıyor? |
|----------|-------------|
| `/admin/stats` | Büyüme istatistikleri: e-posta hunisi, marketing listesi, mağaza listesi |
| `/admin/abuse` | Kötüye kullanım olayları özeti |
| `/admin/ping-ai` | Yapay zeka sağlayıcılarının anlık sağlık ve gecikme testi |
| `/admin/shops-json` | Tüm mağazaların JSON dışa aktarımı |
| `/admin/set-plan` | Mağaza planını manuel değiştirme |

---

## Deployment (Yayınlama)

```
GitHub'daki kod
      ↓
Railway otomatik build + deploy
      ↓
İki ayrı Railway servisi:
  ├── easylisting.app (EUR)
  └── kolaylistele.com (TRY)
Her servisin kendi ortam değişkenleri var
(Stripe fiyat ID'leri, dil ayarları vb.)
```

Kod değişikliği push edildiğinde her iki servis otomatik güncelleniyor.

---

## Özet

**EasyListing / kolaylistele**, yapay zeka destekli bir e-ticaret ilan üreteci. Fotoğraf yükle, platform seç, ilan hazır. Ücretsiz başlanabiliyor, büyüdükçe ücretli plana geçilebiliyor. Etsy, Trendyol ve diğer platformlarla doğrudan entegre çalışıyor. Hem web hem iOS uygulaması var. Güvenlik, kötüye kullanım önleme ve ölçeklenebilirlik başından itibaren tasarıma dahil edilmiş.

---

*Hazırlayan: Sistem otomatik analiz · Haziran 2026*
