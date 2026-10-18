# Instagram İndirici

Herkese açık Instagram profillerindeki fotoğraf ve videoları toplu indiren, Türkçe ve İngilizce arayüzlü **Windows programı**. Sunucu gerekmez; her şey senin bilgisayarında çalışır.

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

## Kullanım

1. Kutuya kullanıcı adını (`@kullaniciadi`) ya da profilin, gönderinin linkini yapıştır.
2. "Sadece bu gönderi" ya da "Profilin tamamı"nı seç, ayarları yap, **İndirmeyi başlat**'a bas.

İndirme sırasında bir Edge penceresi açılıp Instagram'ı kendisi açar. **O pencereyi iş bitene kadar kapatma.**

Dosyalar varsayılan olarak `İndirilenler\Instagram\<kullanıcı adı>\` klasörüne iner:

```
İndirilenler\Instagram\kullaniciadi\
    2026-10-01_gönderi açıklaması_DAbc123XyZ.jpg
    2026-10-03_reels başlığı_DAbd456XyZ.mp4
    2026-10-05_albüm_DAbe789XyZ_01.jpg
    2026-10-05_albüm_DAbe789XyZ_02.mp4
    Hikayeler\2026-10-18_3500000000000000000.jpg
    Öne Çıkanlar\Tatil\2026-07-01_3600000000000000000.jpg
    Profil Fotoğrafı\9988776655.jpg
```

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
