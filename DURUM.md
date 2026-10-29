# DURUM — Instagram İndirici

> Claude'un çalışma notu. Yalnızca `durum` dalında durur; `main`'e, README'ye ve sürüm notlarına girmez.
> Yeni sohbette önce bunu oku, repoyu baştan tarama. Her sürümden sonra güncelle.

Son güncelleme: 2026-10-29 · Son sürüm: **v1.9.4** (2026-10-29)

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
- Klasör (v1.7.0): <kişi>/Gönderiler/Fotoğraflar|Videolar|Albümler/<ad>, Reels (ızgaradaki `product_type=clips` da buraya), Öne Çıkanlar/<başlık>,
  Hikayeler, Profil Fotoğrafı, ops. Yeniden Paylaşımlar / Etiketlenenler, `bilgi.csv`. Eski yerler (kök, Fotoğraflar, Videolar,
  Gönderiler/Videolar) `legacy_stems` ile yeni yerine taşınır (`engine._files_state` 'legacy').
- Geçmiş: `history.py`, `<veri>/gecmis.sqlite3`. `downloaded(user_id, key, content)` (hesaptan bağımsız, profil
  kimliğine bağlı) + `progress(user_id, content)`: `newest` (tam tarama bittiyse "sadece yeni" için) ve
  devam imleci (`rest` / `graphql_in` / `graphql_out`). İmleç, sayfadaki tüm gönderiler inince kaydedilir
  (`engine._advance_pages`); tekrar başlatınca baştaki sayfa tamamen geçmişteyse imlece atlar ("jump").
  Eski `.instagram-indirici.json` sadece okunur.
- Oturum iş ortasında kapanırsa (Instagram ~150+ gönderide yapıyor) bulunanlar iner, not gösterilir.
- Mola: her 5 liste sayfasında 60–120 sn (`INSTAGRAM_INDIRICI_BREAK`); kısıtlanınca 15 dk × en çok 4 tur
  bekleyip devam (`auto_continue` ayarı, `INSTAGRAM_INDIRICI_RESUME_WAIT/_ROUNDS`).
- Video kalitesi (1.3.0): `video_versions` (normal tek parça sürümler) yerine, aynı çözünürlük ya da daha iyisi varsa
  `video_dash_manifest` (MPD) içindeki en büyük tek dosyalı görüntü + en yüksek bit hızlı ses akışı indirilir ve
  yeniden kodlanmadan birleştirilir: Windows `fetcher.fetch_dash` (ffmpeg: `imageio-ffmpeg` paketiyle gelir, EXE'ye
  `--collect-all imageio_ffmpeg`; öz test ffmpeg'i de dener), Android `AndroidMuxer.kt` (MediaExtractor/MediaMuxer;
  JVM testlerinde `TestHooks.muxer`). Parçalı (SegmentTemplate/List) akışlar atlanır. Akış inmezse/birleşmezse normal
  sürüme dönülür (`engine.quality_fallback`). Her video için `engine.quality_dash` / `engine.quality_plain` kayıt satırı.
- Android istek ayarları (1.3.0): liste sayfaları arası 8–15 sn ek bekleme (`pageDelay`), REST sayfa boyutu 12,
  WebView masaüstü Chrome kimliği (`desktopUserAgent`; X-IG-App-ID masaüstü olduğu için). Windows'a dokunulmadı.
- Arayüz (v1.6.0): `instagram_indirici/ui/index.html` tek dosya, Windows + Android ortak (Android derlemede kopyalar).
  Telemet'in görünümü: koyu zemin + turuncu vurgu (açık tema da var), yuvarlak kartlar, alt sekmeler İndir / Geçmiş /
  Ayarlar (`showView`), büyük sayılı ilerleme kartı (`#hero-num`: indirirken yüzde, tararken bulunan gönderi), anahtar
  (switch) satırları, 5 sn kilitli onay penceresi. Test kancaları aynı kaldı: `step1.done`, `start`, `form-error`, `detect`,
  `mode-*`, `notes`, `status`, `account-text`, `login`, `#notes button[data-login]` vb. Ayarlar sekmesindeki satırlar
  (`row-history`, `row-auto`, klasör) artık profil moduna bağlı gizlenmez; yalnız `row-what/content/limit/onlynew` gizlenir.
  Metinler: dinamik anahtar (`'ui.x_' + id`) i18n denetimine takılır → tam anahtar yaz.
- Sihirbaz: ayarlarda `setup_done` (iki platformda sunucu tarafı; Windows'ta arayüz her açılışta başka port kullandığı için
  localStorage güvenilmez). İlk ayarlar gelince `afterFirstSettings`: giriş yapılmış ya da geçmişte kayıt varsa sihirbaz
  açılmaz, `setup_done` kendiliğinden true olur. Adımlar: karşılama(dil) → giriş → (yalnız Windows) klasör → hazır.
  `no_confirm` (silmeden onay sorma) da ayarlarda; eski Android `localStorage ii.noConfirm` ilk açılışta oraya taşınır.
- Geçmiş sekmesi: `/api/history-list` (GET) + `/api/history-forget` (POST user_id). Windows: `History.profiles()/forget()`
  (sqlite `downloaded` tablosundan). Android: `History.profiles()/forget()` + `meta` (user_id → username, last) alanı
  `gecmis.json`'a eklendi; eski dosyalarda zaman yoksa dosyanın değişme zamanı, kullanıcı adı yoksa ilerleme kaydından alınır.
  Android'de profil klasörü bilinmediği için "Klasörü aç" yok (`exists:false`); "Yenileri indir" linki doldurur.
- Windows (v1.6.0) Android'den taşınanlar: `pacing.py` (hız modu; Normal = eski Windows değerleri 2.5–5 sn, 5 sayfada 60–120 sn mola,
  sayfa arası ek bekleme YOK; Dikkatli/Çok dikkatli Android ile aynı), `identity.py` (Bilgisayar = tarayıcının kendi kimliği,
  Android/Tablet = CDP `Network.setUserAgentOverride`, `browser._apply_identity`), `report.py` (rapor, çerez/şifre silinir),
  `server.py` bakım uçları: `/api/report-copy` (PowerShell Set-Clipboard; olmazsa dosya), `/api/report-share` (rapor-*.txt
  dosyası + Explorer'da göster), `/api/clear-cache` (tarayici-*/Cache, Code Cache, GPUCache…), `/api/clear-cookies`
  (tarayici-* profil klasörlerini siler = çıkış; `arayuz-penceresi-*`'ye dokunmaz), `/api/network-settings` (ms-settings:network).
  Testlerde pencere/pano/Explorer açılmasın diye `INSTAGRAM_INDIRICI_NO_OPEN=1`. `INSTAGRAM_INDIRICI_PAGE_DELAY` sayfa beklemesini ezer.
  Öne çıkanlar (`what="highlights"`) `_pace(counts_as_page=False)`: mola sayacına ve sayfa beklemesine girmez (Android v1.4.0 karşılığı).
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

- v1.1.1: öne çıkanlar üçerli istenir, eksik gelen tek tek tekrar istenir; liste reddedilirse profil
  sayfasından okunur; kayıtta öne çıkan başına öğe sayısı.
- v1.2.0: **Android** (`android/`, `io.github.avsessisozx.instagramindirici`, minSdk 29). Douyin Android
  altyapısı + Instagram mantığı Kotlin'de (Insta.kt, Engine.kt, History.kt = gecmis.json, Media.kt).
  Gizli WebView (`WebInstagram`, `IgHolder` uygulamaya ait); giriş/doğrulama penceresi: "Gizle" (küçült,
  arayüzde "Giriş/Doğrulama penceresini aç" düğmesi) + girişte "Kapat". Galeri: DCIM/Instagram/<kişi>/...
  (Windows'la aynı alt klasörler, kullanıcı A seçeneğini seçti). Paylaş menüsü, bildirim, ekran
  yeniden kurulunca iş sürer. Testler: `tests/vectors` (tools/make_vectors.py, Python=Kotlin), JVM
  `EngineTest` (JvmWeb ile sahte sunucuya gerçek HTTP; sahte sunucuda /__ayar, /__sifirla, /__durum),
  emülatörde `EndToEndTest` (15 test, ekran görüntüleri `ci-android-<dal>` dalında).
  CI: windows + android işleri; main'e push → EXE + APK sürümü.

- v1.2.1 (yalnız Android mantığı): kullanıcının Mi 10T Pro'daki ilk denemesi (girişli, @emrdublaj): profil bilgisi +
  profil foto 429, hikaye isteği çalıştı, ama REST `/api/v1/feed/user/` ve `highlights_tray` HTTP 400 "biraz bekle"
  döndü; program her birinde 30+90 sn bekledi, kullanıcı "döngüye girdi" sanıp bıraktı. Düzeltme: REST listenin
  İLK isteği ve öne çıkan listesi reddedilirse beklemeden sıradaki yola (GraphQL → kaydırma / profil sayfası) geçilir;
  liste başladıktan sonraki retlerde eski bekleme sürer. Bekleme sırasında arayüzde geri sayım (waitKind "rate"),
  reddedilen yanıtın ilk 200 karakteri debug kaydına yazılır. Sahte sunucuda `rest_refused`; JVM testi b6.
  Windows (Python) tarafı değişmedi → bu konuda iki sürüm farklı davranıyor (Windows'ta sorun bildirilmedi).

- v1.3.0: en yüksek kalite video (DASH) Windows + Android; Android'de Instagram'ı daha az yoran istekler.
  Telefonda kullanıcı 36 gönderide "Instagram mola istedi" gördü (Windows'ta 200-300 gönderide hiç görmüyordu);
  kod karşılaştırması: bekleme/başlık/sayfa boyutu iki sürümde aynıydı, fark ortam (sıfırdan WebView profili,
  mobil kimlik + masaüstü uygulama kimliği). Kullanıcı ayrıca indirilen videoların Instagram içindeki oynatmadan
  daha küçük/düşük kaliteli olduğunu söyledi → DASH. Testler: 4 birim + 2 uçtan uca (gerçek ffmpeg) + 3 JVM; CI yeşil.
- v1.4.0 (Android): Gelişmiş panel — kimlik seçici (Bilgisayar/Android telefon/Tablet; Identity.kt), istek hızı
  (Normal/Dikkatli/Çok dikkatli; Pacing.kt), ayrıntılı rapor kopyala/paylaş (Report.kt, DeviceInfo.kt; çerez/şifre yok),
  önbelleği temizle, çerezleri sil (çıkış), "Ağ ayarlarını aç" (IP değişimi uygulama içinden mümkün değil; Wi‑Fi↔mobil veri),
  silme onayı (5 sn kilitli "Sil", "Bir daha gösterme", localStorage `ii.noConfirm`, Gelişmiş panelden geri açılır).
  Hayalet mola düzeltmesi: öne çıkanlar (highlights) artık "5 liste sayfasında bir mola" sayacına girmez (`pace(countsAsPage)`).
  Not: i18n kontrol aracı dinamik anahtarı ("ui.identity_" + x) tanımaz → `when` ile tam anahtar yaz.

- v1.4.1 (Android): kullanıcının 1.4.0 raporunda DASH akışı bulundu ama "Failed to add the track to the muxer" ile
  birleşmedi (normal 720x1280 indi). Neden büyük olasılıkla VP9/AV1/Opus (MediaMuxer mp4'e koyamaz). `DashStream.codecs` +
  `muxable`: H.264/H.265 + AAC öncelikli; AndroidMuxer hata iletisi artık reddedilen mime'ı yazar. JVM testi d4.
  Telefonda doğrulanmadı: yeni raporda "Video kalitesi" satırı `quality_dash` mi? Hâlâ aynı hata + mime çıkarsa ona göre ilerle.
  CI'da Windows e2e (gerçek Edge) bir kez geçici hata verdi, yeniden çalıştırınca geçti.

- v1.4.2: günlüğe "Instagram'ın bu video için sunduğu akışlar" + "KARŞILAŞTIRMA" satırı. Telefon raporu (Mi 10T Pro): Instagram bu videoda
  SADECE VP9 veriyor (540p..1080x1920 2.4 Mb/sn, ses mp4a.40.5); normal (progressive) sürüm 720x1280 → gerçek kalite farkı var.
- v1.5.0 (Android): kendi MP4 birleştiricimiz `Mp4Mux.kt` (referans `tools/mp4mux_ref.py`, aynı baytları üretir; fixture'lar
  `android/app/src/test/resources/mp4mux`, JVM testi Mp4MuxTest). Parçalı mp4 görüntü+ses → düz mp4 (moov önde), yeniden kodlama yok.
  `SmartMuxer`: önce MediaMuxer (H.264+AAC), olmazsa Mp4Mux. Akış seçimi: önce çözünürlük, eşitse H.264, sonra bit hızı.
  Yerelde Kotlin derleyicisi: GitHub'dan kotlin-compiler zip indirilip Mp4Mux.kt tek başına derlenebiliyor (Android SDK gerekmeden).
  Telefonda DOĞRULANDI (Mi 10T Pro, Android 12): rapor "Video kalitesi: 1080x1920, 3.6 Mb/sn (en yüksek kalite akışı)";
  dosya 1080x1920 VP9 60 fps + HE-AAC stereo, 4,9 MB, 11 sn; oynuyor, ses sorunsuz.

- v1.6.0: yeni arayüz (Telemet tarzı sekmeler, Geçmiş sekmesi, ilk açılış sihirbazı, büyük sayılı ilerleme) iki platformda;
  Windows'a hız modu, kimlik seçici, ayrıntılı rapor, önbellek/çerez temizleme, silme onayı. Testler: Windows birim 38
  (12 yeni), kimlik için 2 uçtan uca (sahte sunucu görülen User-Agent'ı kaydeder: `fake.user_agents`), Android
  `HistoryProfilesTest` (JVM) + emülatörde `d1_tabsAndHistoryList`, `d2_wizardOnFreshInstall…`, `d3_wizardSkippedForExistingUser`.
  EndToEndTest `setUp` artık `setupDone = true` ile başlar (yoksa sihirbaz ekran görüntülerini kaplar).

- v1.7.0 (iki platform): kullanıcı @heroiiinya ile denedi: Reels sekmesi inmiyor, öne çıkanlar inmiyor (HTTP 500/api_error),
  kategorisiz klasör, geçmişte "indi" görünen dosyalar yok. Yapılanlar: Reels ayrı tarama (`browser.collect_tab`: POST
  `/api/v1/clips/user/` → olmazsa `/<kişi>/reels/` kaydırma yakalama; etiketlenen GET `/api/v1/usertags/{id}/feed/`;
  yeniden paylaşım yalnız kaydırma), yeni klasör düzeni, uzun açıklama `.txt` (ayar `caption_min`, vars. 300, 0=kapalı),
  `verify_files` (geçmiş bilinen gönderilerin dosyası diskte mi; açıkken only_new/resume kapanır, tam tarama),
  `bilgi.csv` (profil başına `;` ayraçlı, UTF-8 BOM), tarih aralığı, duraklat/devam (`/api/pause|resume`), sekme sayaçları,
  inemeyenler listesi + bir otomatik yeniden deneme + "Tekrar dene", hikaye/öne çıkan/Reels için 429'da bekleyip yeniden deneme
  (`_with_rate_wait`), HTTP 5xx iki kez yeniden denenir (`INSTAGRAM_INDIRICI_5XX_WAIT`), öne çıkan ve hikaye için sayfa
  tabanlı yedek (`/stories/highlights/<id>/`, `/stories/<kişi>/`). Android: açıklama `.txt` ve `bilgi.csv` galeriye
  (DCIM) yazılamaz → `Documents/Instagram/<kişi>/<aynı alt yol>` (`MediaSink.writeText/readText/textExists`);
  `MediaStoreSink.moveLegacy` gerçek (RELATIVE_PATH güncellenir). Testler: Windows birim 38, uçtan uca 35 (e1–e9 yeni);
  Android JVM + emülatör yeşil. Sahte sunucuda yeni bayraklar: stories_500, highlights_500, reels_media_fail_n, clips_refused.

## Yarım / sıradaki
- v1.9.4 (iki platform, 2026-10-29): kullanıcı raporu (@artikleopard, 1.9.3, Windows): profil bilgisi 429 verdi → kimlik HTML'den okundu (`sources=html`);
  hikaye/öne çıkan istekleri HTTP 500 verdi (sayfadan okuma yedeği çalıştı, 17 hikaye + öne çıkan indi), sonra gönderi listesinde REST `/feed/user/{id}/` 404 →
  `NotFound` `collect_posts`'ta yakalanmadığı için tüm iş "Aranan şey Instagram'da bulunamadı" (`browser.not_found`) ile durdu (`collect_tab` zaten yakalıyordu).
  Düzeltme: `collect_posts` (Python) / `collectPosts` (Kotlin) `NotFound`'u `ApiError` gibi ele alıp sıradaki yola (GraphQL → kaydırma) geçer.
  Ek: `browser.user_id_from_html` / Kotlin `userIdFromHtml` — kimlik önce kullanıcı adıyla aynı nesnedeki `id|pk|pk_id|profile_id`'den, son çare eski adsız eşleşme.
  Şüphe: 429 sonrası HTML'den okunan kimlik yanlış olabilir (hikaye/öne çıkan 500 + feed 404 birlikte buna işaret ediyor) — kanıtlanmadı.
  DOĞRULANDI (sahte sunucu, Linux Chromium): yeni test `test_c2b_rest_404_uses_graphql` düzeltme olmadan `browser.not_found` ile düşüyor, düzeltmeyle geçiyor;
  `UserIdFromHtmlTests` (3); tüm set 125 test yeşil. Sahte sunucuya `feed_404` bayrağı eklendi.
  DOĞRULANMADI: gerçek Instagram'da (yanlış kimliğin gerçekten sebep olup olmadığı; GraphQL yolunun bu hesapta gönderileri getirmesi); Android Kotlin derlemesi/JVM testi yalnız CI'da.
  Kullanıcıdan bakılacak: yeni raporda `browser.strategy_failed` ("rest: 404") satırı + `posts via=… n=…` debug satırı; gönderiler inecek mi, `user_id=` değeri profilin gerçek kimliği mi.
  Ayrıca raporda: "Geçmişte indi görünüyor ama dosyası yok" iki hikaye (2026-10-28_4017836390488480397, 2026-10-05_…) yeniden indirilecek — bu normal (dosya kontrolü açık).
- v1.9.3 (iki platform): (1) Yeni sürümle ilk açılışta veri sıfırlanır: Windows `reset.py` (`.surum` işareti; silinmeyenler: chrome-eklenti, tarayici-bileseni, calisan.json, ayarlar.json'daki yalnız `out_dir`), Android `Core.resetIfNewVersion/resetData` (ayarlar, durum, geçmiş, giriş durumu; WebView çerezleri MainActivity'de silinir). İndirilenlere dokunulmaz. Ayarlar'a "Her şeyi sıfırla (indirilenler hariç)" düğmesi (`/api/reset-all`). Bedel: her güncellemeden sonra yeniden giriş gerekir. Kullanıcı istedi: program eski sürümün üstüne kurulu gibi davranıyordu.
  (2) `import_session` Instagram yanıtını günlüğe yazar (kind/status/path/err/body[:120]); `current_user` yanıt vermese de sayfa girişli kalıyorsa oturumu kabul eder (test: b0_cookie_2b).
  Kullanıcı testi (1.9.2): Chrome eklentisi kuruldu, "Oturum Instagram İndirici'ye gönderildi" göründü AMA programın kendi Chrome'u `current_user` doğrulamasında "Instagram'a ulaşılamadı ya da yanıt vermedi" (cookie_error) verdi. Sebep o sırada günlükte yoktu; 1.9.3 raporunda `import_session:` debug satırına bak. Şüpheler: ekran dışı pencere, oturumun başka profilde reddi ("useragent mismatch" gibi 400).
  Android Kotlin yalnız CI'da derlendi/test edildi; sıfırlama için Kotlin testi yok (Core Android bağlamı ister).
- v1.9.1: 1.8.0'daki `chromium_sandbox=True` varsayılanı geri alındı (yalnız INSTAGRAM_INDIRICI_SANDBOX=1 ile açılır); tarayıcı beklenmedik kapanırsa günlüğe `browser.died` satırı (neden + saniye) yazılır. Kök sebep doğrulanmadı.
- v1.9.2 (Windows): programın KENDİ giriş penceresi (MODE_LOGIN / `login_session`) kaldırıldı. `/api/login` artık `/api/login-chrome` ile aynı: normal tarayıcıda Instagram açılır, Chrome eklentisi oturumu verir (MODE_COOKIE). UI'da masaüstünde "Instagram'a giriş yap" = Chrome akışı. İndirme tarayıcısı `--window-position=-32000,-32000` ile ekran dışında (INSTAGRAM_INDIRICI_VISIBLE=1 ile görünür). Testlerde `ensure_login` artık MODE_COOKIE kullanır.
  Kullanıcı raporu (1.9.1): giriş penceresinde `Page.goto: net::ERR_ABORTED` → bu yol silindi. Android'e dokunulmadı: orada kendi giriş WebView'i ve sessionid yapıştırma kalır (Chrome eklentisi telefonda çalışmaz).
  Doğrulanmadı (gerçek Chrome/Instagram): ekran dışı pencere Instagram'da sorun çıkarıyor mu; "HeadlessChrome" UA'sı yalnız gizli modda görülür, şu an gizli mod kullanılmıyor.
- v1.9.0 (yalnız Windows): "Chrome'dan giriş yap". Kullanıcı Epic/Steam'deki "tarayıcıdan giriş yap, programa dön" akışını istedi; Instagram'ın resmi
  kapısı yok, Chrome çerezi dışarıdan okutmuyor → MV3 Chrome eklentisi `instagram_indirici/chrome_eklenti/` (manifest, content.js, background.js).
  Akış: `/api/login-chrome` tek kullanımlık kod üretir (10 dk) ve `https://www.instagram.com/#ii=<port>.<kod>` adresini varsayılan tarayıcıda açar;
  content.js kodu okuyup adresten siler; background.js `chrome.cookies` ile sessionid/csrftoken/ds_user_id/mid/ig_did/datr/ig_nrcb'yi (girişli değilse
  giriş olunca `cookies.onChanged` ile) `POST http://127.0.0.1:<port>/api/extension-session`'a yollar; sunucu kod + `chrome-extension://` kökeni + tek
  kullanımı denetler, MODE_COOKIE işini başlatır (aynı içe aktarma/doğrulama). Başka uçlara eklenti kökeni kabul edilmez.
  `/api/extension-open` eklentiyi `%APPDATA%…/chrome-eklenti` klasörüne kopyalar (EXE'ye `--add-data chrome_eklenti`); kullanıcı Chrome'da
  chrome://extensions > Geliştirici modu > Paketlenmemiş öğe yükle ile bir kere kurar. Cihaz çerezleri (mid, ig_did, datr) de alınır → program tarayıcısı aynı cihaz sayılsın.
  Testler: `tests/test_extension.py` (gerçek Chromium'a eklenti yükler; sahte Instagram + gerçek sunucu; CI'da ayrı `eklenti` Linux işi, release buna bağlı),
  sunucu güvenlik birim testleri (ChromeLoginEndpointTests). Android'de Chrome eklenti desteklemediği için yalnız yapıştırma yöntemi var.
  GERÇEK CHROME + GERÇEK INSTAGRAM'DA DENENMEDİ. Kullanıcıdan bakılacak: eklenti kuruluyor mu (Chrome "geliştirici modu" uyarısı normal), Chrome varsayılan tarayıcı mı,
  bağlantı Chrome'da açılınca üstte yeşil "gönderildi" şeridi çıkıyor mu, program oturumu `cookie_ok` ile kabul ediyor mu ya da Instagram yine doğrulama (challenge) istiyor mu.
  Chrome Web Store'a yüklenmedi (ücret/inceleme); paketlenmemiş yükleme her Chrome açılışında "geliştirici modu eklentileri" uyarısı gösterebilir.
- v1.8.0 (iki platform): kullanıcı Windows'ta uygulamanın giriş penceresinde Instagram'ın "devam etmek için başka cihazda giriş yap" kilidine takıldı
  (normal Chrome'da giriş sorunsuzdu; pencere her seferinde sıfırdan, tanınmayan tarayıcı sayılıyor; ekranda Chrome'un `--no-sandbox` uyarısı vardı).
  Yapılanlar: Ayarlar > Hesap > "Giriş yapamıyorum": kullanıcı tanıdığı tarayıcıdan `sessionid` değerini yapıştırır (`/api/login-cookie`, `MODE_COOKIE`,
  `browser.parse_session_input` = Kotlin `SessionInput`, ortak vektör `tests/vectors/session.json`). `import_session`: çerezleri yazar, `/api/v1/accounts/current_user/`
  ile doğrular (ok/expired/challenge/error), başarısızsa eski oturumu geri yükler; değer günlüğe/rapora yazılmaz. Windows'ta `chromium_sandbox=True`
  (`use_chromium_sandbox`, Linux'ta kapalı; `INSTAGRAM_INDIRICI_SANDBOX=0/1`). Giriş penceresi açıkken ipucu metni. Testler: 15 birim (SessionImportTests, CookieEndpointTests),
  3 uçtan uca (b0_cookie_*), JVM `EngineTest.a4_cookie*` + `SharedVectorsTest.session`; CI yeşil.
  Chrome/Edge çerezini programın kendisi okumuyor: Windows'ta Chrome'un çerez şifrelemesi (app-bound) buna izin vermiyor; bu yüzden yapıştırma yöntemi.
  GERÇEK INSTAGRAM'DA DENENMEDİ: yapıştırılan kodla giriş (özellikle Windows'ta `current_user` doğrulaması ve Android WebView `CookieManager` yazımı).
  Kullanıcının yedek hesabı kimlik/selfie doğrulaması istiyor (VPN/yeni IP + otomatik istek şüphesi); doğrulamayı atlatma yardımı verilmedi.
- v1.7.0 (düzeltme, aynı sürüm numarası): @vv_sechko raporunda Instagram hız sınırına takıldı (429, "spam" 400) ve öne çıkan liste isteğine yanıt gelmedi;
  her istek 45 sn + 2 tekrar beklediği için 21 öne çıkan ~40 dk sürdü. Artık `call(..., quick=True)`: 20 sn, tekrarsız; ilk zaman aşımından sonra
  `api_down` olur, liste yolu bırakılır ve doğrudan `/stories/highlights/<id>/` sayfasına geçilir (Python `highlight_reels`, Kotlin `highlightReels`).
  Testler: test_e5b (Python), e5b (Kotlin; JvmWeb artık fetch zaman aşımına uyuyor). Hız sınırındayken sayfa yedeği de yavaş (60 sn "Sayfa yavaş açılıyor");
  bu durumda kullanıcı 1-2 saat dinlendirmeli. Gönderi listesi / Reels doğrulaması hâlâ bekliyor.
- v1.7.0 GERÇEK INSTAGRAM'DA DENENMEDİ (buradan erişim yok): Reels `clips/user` POST, `usertags` GET, `/reels|tagged|reposts/` sayfa
  kaydırma, `/stories/highlights/<id>/` sayfa yedeği yalnız sahte sunucuda sınandı. Kullanıcıdan bakılacak: @heroiiinya'da Reels
  klasörü Reels sekmesindeki sayıyla uyuşuyor mu, Öne Çıkanlar iniyor mu (Ayrıntılar'da `highlight_from_page` / HTTP 500 satırı),
  Android'de Documents/Instagram altındaki .txt ve bilgi.csv. Ters giderse rapordaki `reels via=… reason=…` debug satırına bak.
  Yeniden paylaşımlar yalnız kaydırmayla toplanır (en zayıf yol).
- v1.6.0 gerçek kullanımda denenmedi (kullanıcıdan bakılacaklar): (1) Windows'ta yeni arayüz Edge `--app` penceresinde nasıl
  görünüyor; (2) Windows'ta "Android telefon"/"Tablet" kimliği gerçek Instagram'da doğrulama istiyor mu (varsayılan Bilgisayar);
  (3) Android'de Geçmiş sekmesi eski kayıtlarda tarihleri doğru gösteriyor mu (eski kayıtta zaman yok → dosya zamanı);
  (4) Windows Dikkatli modlar kaç gönderide mola getiriyor.
- Windows'a taşınmayan: Android v1.2.1 "REST listenin ilk isteği reddedilirse beklemeden sıradaki yola geç" davranışı (Windows'ta
  essential çağrı hâlâ 30+90 sn bekler; kullanıcı Windows'ta sorun bildirmedi).
- v1.3.0 gerçek Instagram'da denenmedi (buradan erişim yok). Kullanıcıdan bakılacaklar: (1) indirilen videoda Ayrıntılar'daki
  "Video kalitesi" satırı: `quality_dash` çıkıyorsa yüksek kalite alınmış; hep `quality_plain` çıkıyorsa Instagram'ın liste
  yanıtında `video_dash_manifest` yok → sıradaki adım: gönderiyi tek tek `/api/v1/media/{id}/info/` ile istemek (istek
  sayısını artırır, hız sınırına dikkat). (2) Android'de "mola" ne kadar geç geliyor. (3) Masaüstü kimliğine geçişte
  Instagram doğrulama isteyebilir; sorun çıkarsa `WebInstagram.desktopUserAgent` yerine `chromeUserAgent` kullan.
- v1.2.1 Android'in telefonda denenmesi bekleniyor: GraphQL (doc_id 28975909992013618) ya da kaydırma bu oturumda
  gönderi getiriyor mu? Getirmiyorsa debug kaydındaki Instagram yanıtına bak; sıradaki şüpheliler: WebView'in mobil
  kimliği (mobil Chrome UA + masaüstü X-IG-App-ID 936619743392459), eski X-ASBD-ID 129477 (instaloader 198387 kullanıyor).
- (eski not) v1.2.0 Android'in kullanıcının telefonunda (Xiaomi Mi 10T Pro) denenmesi bekleniyor. Windows 1.1.x kullanıcıda çalışıyor (gerçek Instagram'a buradan erişim yok). Gönderi listesi de
  reddedilirse kaydındaki "isteğini kabul etmedi (HTTP …)" satırlarına bak.

- Doğrulanmadı (gerçek Instagram): DASH kalitesi (günlükte hep "quality_plain" çıkıyorsa liste yanıtında manifest yok →
  her gönderiyi `/api/v1/media/{id}/info/` ile iste); kimlik/hız modu mola ve 429'u azaltıyor mu. Kullanıcı 1–2 saat dinlendirip denemeli.

## Bilinen sorunlar
- Kayıtlı açık hata yok. Instagram doc_id'leri zamanla değişebilir (o zaman REST/kaydırma yedekleri devreye girer).

## Kurallar
- Sürüm vermeden önce kendin test et; kullanıcıya bozuk sürüm test ettirme
- Test + CI geçtiyse düzeltmeyi sormadan yayınla (kullanıcı her seferinde "yayınlayayım mı" sorulmasından hoşlanmıyor)
- Sürüm notlarına ve commit mesajlarına sohbet / oturum bağlantısı (`Claude-Session:`) ekleme
- Önce `gelistirme` dalında CI'ı geçir, sonra `main`'e al (main'e push sürüm yayınlar)
