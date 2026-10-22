# Instagram İndirici

Herkese açık Instagram profillerindeki fotoğraf ve videoları toplu indiren, Türkçe ve İngilizce arayüzlü **Windows programı** ve **Android uygulaması**. Sunucu gerekmez; her şey senin cihazında çalışır.

- **Profilin tamamı:** bir kişinin bütün gönderilerini (fotoğraf, video, reels, kaydırmalı albümler) toplu indirir.
- **Hikayeler, öne çıkanlar ve profil fotoğrafı** da indirilebilir (hikaye ve öne çıkanlar için giriş gerekir).
- **Tek gönderi:** bir gönderi, reels, hikaye ya da öne çıkan linkini yapıştırıp sadece onu indirebilirsin.
- **İçerik seçimi:** fotoğraf ve video, sadece video ya da sadece fotoğraf. İstersen sadece en yeni 12, 30, 50… gönderi.
- En yüksek kaliteyi indirir. Daha önce inenleri tekrar indirmez; yarıda kalan işe kaldığı yerden devam edersin.
- **Sadece yenileri:** bir profili bir kez tamamen indirdikten sonra tekrar başlattığında sadece yeni gönderilere bakar.

## İndirme ve kurulum

1. [Releases](../../releases) sayfasından en yeni `InstagramIndirici.exe` dosyasını indir.
2. Çift tıklayıp aç. Kurulum gerekmez.
3. İlk açılışta Windows **"Bilgisayarınız korundu"** diyebilir: **Ek bilgi → Yine de çalıştır**. (Program imzalı olmadığı için bu uyarı çıkar.)

Gereken tek şey Microsoft Edge (Windows 10/11'de zaten yüklü) ya da Google Chrome. Edge'in kurulumu bozuksa program Chrome'u ya da kendi indirdiği tarayıcıyı kullanır.

### Android
1. Telefondan [Releases](../../releases) sayfasını aç, `InstagramIndirici-<sürüm>.apk` dosyasına dokun.
2. Telefon "bilinmeyen kaynaktan yükleme" izni isterse ver, sonra **Yükle**.
3. Yeni sürümler eskisinin üzerine kurulur; ayarlar, geçmiş ve Instagram girişi silinmez.

Android 10 ve üstü gerekir. Her şey uygulamanın içinde olur, ayrı tarayıcı açılmaz. Instagram'a giriş ya da doğrulama gerektiğinde uygulamanın içinde bir pencere açılır: **Gizle** pencereyi küçültür (iş beklemeye devam eder, arayüzdeki düğmeyle tekrar açılır), giriş penceresindeki **Kapat** girişten vazgeçer.

En kolay yol: Instagram'da bir gönderide ya da profilde **Paylaş → Instagram İndirici**; link kutuya kendiliğinden gelir. İndirme sürerken uygulamadan çıkabilir ya da ekranı kapatabilirsin; ilerleme bildirimde görünür. Dosyalar galeride **DCIM › Instagram › <kullanıcı adı>** altında, Windows'takiyle aynı alt klasörlere (Gönderiler, Reels, Hikayeler, Öne Çıkanlar, Profil Fotoğrafı) iner.

## Kullanım

1. Kutuya kullanıcı adını (`@kullaniciadi`) ya da profilin, gönderinin linkini yapıştır.
2. "Sadece bu gönderi" ya da "Profilin tamamı"nı seç, ayarları yap, **İndirmeyi başlat**'a bas.

İndirme sırasında bir Edge penceresi açılıp Instagram'ı kendisi açar. **O pencereyi iş bitene kadar kapatma.**

Dosyalar varsayılan olarak `İndirilenler\Instagram\<kullanıcı adı>\` klasörüne, Instagram sayfasındaki sekmeler gibi ayrı klasörlere iner:

```
İndirilenler\Instagram\kullaniciadi\
    Gönderiler\Fotoğraflar\2026-10-01_gönderi açıklaması_DAbc123XyZ.jpg
    Gönderiler\Videolar\2026-10-02_video gönderi_DAbf321XyZ.mp4
    Gönderiler\Albümler\2026-10-05_albüm_DAbe789XyZ\2026-10-05_albüm_DAbe789XyZ_01.jpg
    Reels\2026-10-03_reels başlığı_DAbd456XyZ.mp4
    Reels\2026-10-03_reels başlığı_DAbd456XyZ.txt        (açıklama uzunsa)
    Öne Çıkanlar\Tatil\2026-07-01_3600000000000000000.jpg
    Hikayeler\2026-10-18_3500000000000000000.jpg
    Profil Fotoğrafı\9988776655.jpg
    Yeniden Paylaşımlar\...     (isteğe bağlı)
    Etiketlenenler\...          (isteğe bağlı)
    bilgi.csv                    (tarih, bağlantı, beğeni, yorum sayısı)
```

- **Reels** ayrı bir sekme olarak taranır; profil ızgarasında görünen klipler de aynı klasöre iner, bir klip iki kez inmez.
- **Uzun açıklamalar** (varsayılan 300 karakterden uzunsa; Ayarlar'dan değişir, 0 = kapalı) videonun/fotoğrafın yanına aynı adlı `.txt` olarak yazılır.
- **Eksik dosyaları tamamla** (varsayılan açık): geçmişte "indi" görünen ama klasörde olmayan dosyalar bulunup yeniden indirilir.
- İnemeyen gönderiler listelenir, bir kez kendiliğinden yeniden denenir ve **İnemeyenleri tekrar dene** düğmesiyle tekrar başlatılabilir. İndirme **duraklatılıp** devam ettirilebilir.
- İsteğe bağlı tarih aralığı: sadece belli tarihler arasındaki gönderiler.
- Android'de galeri yalnız görüntü/videoyu kabul ettiği için `.txt` açıklamaları ve `bilgi.csv` **Documents › Instagram › <kullanıcı adı>** altında aynı alt klasör yapısıyla durur.
- Eski sürümlerin `Fotoğraflar`, `Videolar` ve kök klasördeki dosyaları ilk indirmede yeni yerlerine taşınır (yeniden inmez).

## Kaldığı yerden devam ve indirme geçmişi

Instagram toplu gezinmede bir süre sonra mola istiyor ya da oturumu kapatıp yeniden giriş istiyor (genelde birkaç yüz gönderiden sonra). Program bunun için:

- **İndirme geçmişi tutar.** Hangi profilden hangi gönderinin indiğini bilir. Geçmiş Instagram hesabına değil profile bağlıdır: aylar sonra, başka bir hesapla giriş yapıp, hatta başka bir klasöre indirsen bile önceden inenler tekrar inmez.
- **Listede nerede kaldığını hatırlar.** Tekrar başlattığında önce yeni gönderilere bakar, sonra yarıda kalan yere atlayıp oradan devam eder; baştan yüzlerce gönderiyi tekrar taramaz.
- **Kendi kendine mola verir.** Belli aralıklarla kısa molalar verir. Instagram yine de durdurursa 15 dakika bekleyip kendiliğinden devam eder (ayarlardan kapatılabilir).
- Instagram oturumu kapatırsa bulduklarını indirir; tekrar giriş yapıp (istersen başka hesapla) aynı linkle başlatman yeterli.

Geçmişi ayarlardaki **Geçmişi temizle** ile silebilirsin (indirilen dosyalar silinmez).

## Giriş yapmak gerekiyor mu?

Giriş yapmadan da çalışır ama Instagram, giriş yapmayanlara çoğu profilin sadece ilk gönderilerini gösteriyor; hikaye ve öne çıkanları hiç göstermiyor. Hepsini indirmek için programın altındaki **Instagram'a giriş yap** ile bir kez giriş yap. Giriş, programın kendi tarayıcı penceresinde Instagram'ın kendi sayfasında yapılır; şifren programa girmez, oturum sadece senin bilgisayarında durur.

**Ana hesabın yerine yedek bir hesap kullanman daha güvenli.** Instagram, çok sayıda indirme yapan hesapları geçici olarak kısıtlayabiliyor.

## Sık sorulanlar

**"Instagram doğrulama istiyor" yazıyor.**
Instagram bazen "bu sen misin?" diye kod ya da onay ister. Açılan Edge penceresinde doğrulamayı tamamla; program bekler ve sonra kendiliğinden devam eder.

**"Instagram yavaşlamamızı istedi" yazıyor.**
Instagram kısa sürede çok istek gelince bir süre bekletir. Program önce kendisi bekleyip tekrar dener. Yine olmazsa yarım saat kadar sonra aynı linkle tekrar başlat; inenler tekrar inmez. Program Instagram'ı kızdırmamak için listeyi bilerek yavaş tarar.

**Gizli hesapları indirebilir mi?**
Sadece giriş yaptığın hesap o gizli hesabı takip ediyorsa.

## Geliştirme

```
pip install -r requirements.txt
python main.py
```

Testler: `python tools/run_tests.py tests.test_units` (hızlı) ve `python tools/run_tests.py tests.test_integration` (gerçek tarayıcı + sahte Instagram sunucusu). Çeviriler `ceviriler/` klasöründe, her dil ayrı dosyada; `python tools/check_i18n.py` eksikleri gösterir.

Bu program Instagram ya da Meta ile ilgili değildir. Sadece herkese açık içeriği kişisel kullanım için indirmek amacıyla yazılmıştır; indirdiğin içeriğin haklarına ve Instagram kullanım koşullarına dikkat etmek kullanıcının sorumluluğundadır.

## Lisans

MIT
