# DURUM — Instagram İndirici

> Claude'un çalışma notu. Yalnızca `durum` dalında durur; `main`'e, README'ye ve sürüm notlarına girmez.
> Yeni sohbette önce bunu oku, repoyu baştan tarama. Her sürümden sonra güncelle.

Son güncelleme: 2026-10-18 · Son sürüm: **v1.1.0** (2026-10-18)

## Ne yapıyor
Herkese açık Instagram profillerini toplu indirir: gönderiler/reels/albümler, hikayeler, öne çıkanlar,
profil fotoğrafı. Tek gönderi / reels / hikaye / öne çıkan / paylaşım linki de olur. Türkçe + İngilizce arayüz.
Kullanıcı şimdilik kendisi kullanacak; sunucu gerektirmediği için ileride açık kaynak kalabilir.

## Teknik
- Douyin İndirici'nin (avsessisozx/douyin-indirici) iskeleti: yerel sunucu + Edge `--app` penceresi,
  Playwright ile kalıcı profilli Edge/Chrome/kendi Chromium'u, `winbrowsers.py`, çeviri altyapısı aynen.
- Windows: Python (`main.py`, `instagram_indirici/`), EXE GitHub Actions'ta derlenir; `__version__` → `instagram_indirici/__init__.py`
- Veri toplama `browser.py`: instagram.com sayfanın içinden `fetch` (X-IG-App-ID 936619743392459, csrftoken çerezi).
  Profil: sayfaya gömülü JSON → `web_profile_info` → `topsearch`. Gönderiler sırasıyla:
  giriş varsa REST `/api/v1/feed/user/{id}/` → GraphQL doc_id 28975909992013618 → profil sayfasını kaydırma;
  giriş yoksa gömülü ilk 12 + GraphQL doc_id 7950326061742207 → kaydırma → "giriş duvarı" notu.
  Hikaye: `/api/v1/feed/reels_media/?reel_ids=ID`; öne çıkan: `highlights_tray` + reels_media (5'erli).
  Tek gönderi: `/api/v1/media/{id}/info/` ya da gönderi sayfasındaki gömülü JSON.
  Eski biçimde videosu gelmeyen gönderiler sonradan tek tek açılır (`engine._repair`).
- `media.py` iki biçimi de (v1 ve eski GraphQL) tanır, yanıtın içinde nerede olduklarına bakmaz.
- Hız sınırı: istekler arası 2.5–5 sn. 429 / "Please wait": sadece gönderi listesinde (essential) 30/90 sn bekleyip dener;
  profil bilgisi/arama/hikaye vb. hemen vazgeçip başka yoldan devam eder. Liste kısıtlanırsa sıradaki yola geçer.
  Doğrulama (challenge) → pencereyi öne getirip kullanıcıyı bekler.
- Giriş: "Instagram'a giriş yap" pencerede instagram.com/accounts/login açar; `sessionid` çerezi gelince kendisi kapanır.
  Durum `oturum.json`'da (arayüz altında yeşil nokta).
- Klasör: <kişi>/Fotoğraflar, Videolar, Hikayeler, Öne Çıkanlar/<başlık>, Profil Fotoğrafı (1.1.0'dan beri;
  1.0.x'in kök klasördeki dosyaları `legacy_stem` ile yeni yerine taşınır).
- Geçmiş: `history.py`, `<veri>/gecmis.sqlite3`. `downloaded(user_id, key, content)` (hesaptan bağımsız, profil
  kimliğine bağlı) + `progress(user_id, content)`: `newest` (tam tarama bittiyse "sadece yeni" için) ve
  devam imleci (`rest` / `graphql_in` / `graphql_out`). İmleç, sayfadaki tüm gönderiler inince kaydedilir
  (`engine._advance_pages`); tekrar başlatınca baştaki sayfa tamamen geçmişteyse imlece atlar ("jump").
  Eski `.instagram-indirici.json` sadece okunur.
- Oturum iş ortasında kapanırsa (Instagram ~150+ gönderide yapıyor) bulunanlar iner, not gösterilir.
- Mola: her 5 liste sayfasında 60–120 sn (`INSTAGRAM_INDIRICI_BREAK`); kısıtlanınca 15 dk × en çok 4 tur
  bekleyip devam (`auto_continue` ayarı, `INSTAGRAM_INDIRICI_RESUME_WAIT/_ROUNDS`).
- Çeviriler: `ceviriler/tr.json`, `en.json` (`tools/check_i18n.py`)
- Testler: `tests/test_units.py`, `tests/test_integration.py` (gerçek tarayıcı + `tests/fake_instagram.py`)
- CI: `build.yml` her dalda (testler + EXE + EXE öz testi); `main`'e push → Release.
  `tools/canli_deneme.py` gerçek Instagram'a giriş yapmadan bilgi amaçlı deneme yapar (sonuç: Actions özeti).

## Yapılanlar
- v1.0.0: Windows EXE. Sahte sunucuya karşı 16 uçtan uca + 19 birim testi geçiyor (Linux Chromium ve CI'da Edge).
- CI'daki gerçek Instagram denemesi: GitHub'ın sunucu adresine Instagram profil sayfasını hep HTTP 429 (boş sayfa) döndürüyor;
  bu yüzden gerçek doğrulama sadece kullanıcının bilgisayarında yapılabiliyor. 429 gelince program bekleyip tekrar deniyor.
- Dallar: `gelistirme` (çalışma), `main` (sürüm), `ci-sonuc-*` (CI deneme çıktısı), `durum` (bu not).
  Varsayılan dal `gelistirme` kaldı (ilk push); proxy ayar değiştirmeye izin vermedi → kullanıcı Settings'ten `main` yapabilir.

- v1.0.1: Kullanıcının ilk gerçek denemesinde (girişli, Chrome; Edge'i bozuk) profil bilgisi isteği hep
  "biraz bekle" döndü → v1.0.0 10 dk bekleyip vazgeçti. Düzeltildi; kayıtta reddedilen istek + HTTP kodu yazıyor.

- v1.1.0: alt klasörler, geçmiş, kaldığı yerden devam, otomatik mola (kullanıcı 150 gönderide oturum
  kapanması yaşadı). 21 uçtan uca + 21 birim testi.

## Yarım / sıradaki
- v1.1.0'ın kullanıcıda denenmesi bekleniyor (gerçek Instagram'a buradan erişim yok). Gönderi listesi de
  reddedilirse kaydındaki "isteğini kabul etmedi (HTTP …)" satırlarına bak.
- Windows'ta çalışırsa: Android sürümü (Douyin'deki gibi Kotlin + gizli WebView, aynı API yolları).

## Bilinen sorunlar
- Kayıtlı açık hata yok. Instagram doc_id'leri zamanla değişebilir (o zaman REST/kaydırma yedekleri devreye girer).

## Kurallar
- Sürüm vermeden önce kendin test et; kullanıcıya bozuk sürüm test ettirme
- Sürüm notlarına ve commit mesajlarına sohbet / oturum bağlantısı (`Claude-Session:`) ekleme
- Önce `gelistirme` dalında CI'ı geçir, sonra `main`'e al (main'e push sürüm yayınlar)
